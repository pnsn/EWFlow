from wyrm.core._base import Wyrm
from wyrm.core.buffer.structure import TieredBuffer
from wyrm.core.buffer.trace import TraceBuffer
from wyrm.core.window.instrument import InstrumentWindow
import wyrm.util.compatability as wcc
import seisbench.models as sbm
from obspy import UTCDateTime
from collections import deque
from copy import deepcopy


class WindowWyrm(Wyrm):
    """
    The WindowWyrm class takes windowing information from an input
    seisbench.models.WaveformModel object and user-defined component
    mapping and data completeness metrics and provides a pulse method
    that iterates across entries in an input RtInstStream object and
    generates windowed copies of data therein that pass data completeness
    requirements. Windowed data are formatted as MLInstWindow objects and
    staged in the WindWyrm.queue attribute (a deque) that can be accessed
    by subsequent data pre-processing and ML prediction Wyrms.
    """

    def __init__(
        self,
        code_map={"Z": "Z3", "N": "N1", "E": "E2"},
        completeness={"Z": 0.95, "N": 0.8, "E": 0.8},
        missing_component_rule="zeros",
        model_name="EQTransformer",
        target_sr=100.0,
        target_npts=6000,
        target_overlap=1800,
        target_blinding=(500,500),
        target_order="ZNE",
        max_pulse_size=20,
        debug=False,
    ):
        """
        Initialize a WindowWyrm object

        :: INPUTS ::
        -- Window Generation --
        :param code_map: [dict] dictionary with keys "Z", "N", "E" that have
                        iterable sets of character(s) for the SEED component
                        codes that should be aliased to respectiv keys
        :param trace_comp_fract :[dict] dictionary of fractional thresholds
                        that must be met to consider a given component sufficiently
                        complete to trigger generating a new InstrumentWindow
        :param missing_component_rule: [str]
                        string name for the rule to apply in the event that
                        horizontal data are missing or too gappy
                            Supported: 'zeros', 'clonez','clonehz'
                        also see wyrm.core.window.instrument.InstrumentWindow
        :param model_name: [str]
                        Model name to associate this windowing parameter set to
                        e.g., EQTransformer, PhaseNet
        :param target_sr: [float]
                        Target sampling_rate to pass to InsturmentWindow init
        :param target_npts: [int]
                        Target temporal samples to pass to InstrumentWindow init
        :param target_overlap: [int]
                        Target overlap between sequential windows. Used alongside
                        target_sr and target_npts to determine window advances
                        between pulse iterations
        :param target_blinding:  [2-tuple] of [int]
                        left and right blinding sample amounts for stacking ML
                        predictions. This is also used to determine trace
                        completeness. 
        :param target_order: [str]
                        Target component order to pass to InstrumentWindow init
        :param max_pulse_size: [int]
                        Maximum number of sweeps this Wyrm should conduct over
                        pulse input x (TieredBuffer) buffers to check if each
                        buffer can produce a new window. Generally this will be
                        a low number [1, 20].
        :param debug: [bool] - run in debug mode?
        """
        super().__init__(max_pulse_size=max_pulse_size, debug=debug)

        if not isinstance(code_map, dict):
            raise TypeError('code_map must be type dict')
        elif not all(_c in 'ZNE' for _c in code_map.keys()):
            raise KeyError('code_map keys must comprise "Z", "N", "E"')
        elif not all(isinstance(_v, str) and _k in _v for _k, _v in code_map.items()):
            raise SyntaxError('code_map values must be type str and include the key value')
        else:
            self.code_map = code_map

        if not isinstance(completeness, dict):
            raise TypeError('completeness must be type dict')
        elif not all(_c in 'ZNE' for _c in completeness.keys()):
            raise KeyError('completeness keys must comprise "Z", "N", "E"')
        elif not all (0 <= _v <= 1 for _v in completeness.values()):
            raise ValueError('completeness values must fall in the range [0, 1]')
        else:
            self.completeness = completeness
        
        if not isinstance(missing_component_rule, str):
            raise TypeError('missing_component_rule must be type str')
        elif missing_component_rule.lower() not in ['zeros','clonez','clonehz']:
            raise ValueError(f'missing_component_rule {missing_component_rule} not supported')
        else:
            self.mcr = missing_component_rule.lower()

        if not isinstance(model_name, str):
            raise TypeError('model_name must be type str')
        else:
            self.model_name = model_name
    
        self.target_sr = wcc.bounded_floatlike(
            target_sr,
            name='target_sr',
            minimum=0,
            maximum=None,
            inclusive=False
        )

        self.target_npts = wcc.bounded_intlike(
            target_npts,
            name='target_npts',
            minimum=0,
            maximum=None,
            inclusive=False
        )

        self.target_overlap = wcc.bounded_intlike(
            target_overlap,
            name='target_overlap',
            minimum=0,
            maximum=None,
            inclusive=False
        )

        if not isinstance(target_blinding, (list, tuple)):
            raise TypeError('target_blinding must be type list or tuple')
        elif len(target_blinding) != 2:
            raise ValueError('target_blinding must be a 2-element list/tuple')
        else:
            lblnd = wcc.bounded_intlike(target_blinding[0],
                                        name='target_blinding[0]',
                                        minimum=0,
                                        maximum=self.t_npts/2,
                                        inclusive=True)
            rblnd = wcc.bounded_intlike(target_blinding[1],
                                        name='target_blinding[1]',
                                        minimum=0,
                                        maximum=self.t_npts/2)
            self.t_blinding = (lblnd, rblnd)
        
        if not isinstance(target_order, str):
            raise TypeError('target_order must be type str')
        elif not all(_c.upper() in 'ZNE' for _c in target_order):
            raise ValueError('target_order must comprise "Z", "N", "E"')
        else:
            self.target_order = target_order.upper()

        # Set (non-UTCDateTime) default starttime for new windowing indexing
        self.default_starttime = None
        self._index_template = {
            "last_starttime": self.default_starttime,
            "next_starttime": self.default_starttime,
        }

        # Data Storage and I/O Type Attributes
        # Create index for holding instrument window starttime values
        self.window_tracker = {}

        # Create queue for output collection of
        self.queue = deque([])

        # Update input and output types for TubeWyrm & compatability references
        self._update_io_types(itype=(TieredBuffer, TraceBuffer), otype=(deque, InstrumentWindow))

    ################
    # PULSE METHOD #
    ################
        
    def pulse(self, x):
        """
        Conduct up to the specified number of iterations of
        self._process_windows on an input RtInstStream object
        and return access to this WindWyrm's queue attribute

        Includes an early termination trigger if an iteration
        does not generate new windows.

        :: INPUT ::
        :param x: [wyrm.structure.stream.RtInstStream]

        :: OUTPUT ::
        :return y: [deque] deque of InstWindow objects
                    loaded with the appendleft() method, so
                    the oldest messages can be removed with
                    the pop() method in a subsequent step
        """
        _ = self._matches_itype(x, raise_error=True)
        for _ in range(self.max_pulse_size):
            nnew = self.process_windows(x)
            if nnew == 0:
                break
        # Return y as access to WindWyrm.queue attribute
        y = self.queue
        return y

    # ################ #
    # CORE SUBROUTINES #
    # ################ #

    def process_window(self, input, pad=True, wgt_taper_sec='blinding', wgt_taper_type='cosine'):
        if not self._matches_itype(input):
            raise TypeError(f'input must be type {self._in_type [0]} - not {type(input)}')
        elif not self._matches_itype(input.buff_class):
            raise TypeError(f'input.buff_class must be {self._in_type[1]} - not {type(input)}')
        
        nnew = 0

        for k0 in input.keys():
            _branch = input[k0]
            if k0 not in self.window_tracker.keys():
                self.window_tracker.update({k0: self._index_template.copy()})
                
            next_ts = self.window_tracker[k0]

            if next_ts == self.default_starttime:
                for _c in self.code_map['Z']:
                    if _c in _branch.keys():
                        if len(_branch[_c]) > 0:
                            first_ts = _branch[_c].stats.starttime
                            self.window_tracker[k0].update({'next_starttime': first_ts})
                            next_ts = first_ts
                            break

            if isinstance(next_ts, UTCDateTime):
                data_ts = None
                data_te = None
                for k1 in _branch.keys():
                    if len(_branch[k1]) > 0:
                        data_ts = _branch[k1].stats.starttime
                        data_te = _branch[k1].stats.endtime
                        break
                if data_ts <= next_ts < data_te:
                    pass
                elif next_ts > data_te:

    def _branch2instrumentwindow(
        self,
        branch,
        next_window_starttime,
        pad=True,
        extra_sec=1.0,
        wgt_taper_sec=0.0,
        wgt_taper_type="cosine",
        index=None
    ):
        """
        Using a specified candidate window starttime and windowing information
        attributes in this WindWyrm, determine if and input branch from a
        RtInstStream object has enough data to generate a viable window

        :param branch: [dict] of [wyrm.structures.rtbufftrace.RtBuffTrace] objects
                            with keys corresponding to RtBuffTrace datas' component
                            code (e.g., for BHN -> branch = {'N': RtBuffTrace()})
        :param next_window_starttime: [UTCDateTime] start time of the candidate window
        :param pad: [bool] should data windowed from RtBuffTrace objects be padded
                           (i.e., allow for generating masked data?)
                           see obspy.core.trace.Trace.trim() for more information
        :param extra_sec: [None] or [float] extra padding to place around windowed
                            data. Must be a positive value or None. None results in
                            extra_sec = 0.
                            NOTE: Extra samples encompassed by the extra_sec padding
                            on each end of windows are only included after a candidate
                            window has been deemed to have sufficient data. They do not
                            factor into the determination of if a candidate window
                            is valid
        :param wgt_taper_sec: [str] or [float-like] amount of seconds on each end
                            of a candidate window to downweight using a specified
                            taper function when assessing the fraction of the window
                            that contains valid data.
                            Supported string arguments:
                                'blinding' - uses the blinding defined by the ML model
                                associated with this WindWyrm to set the taper length
                            float-like inputs must be g.e. 0 and finite
        :param wgt_taper_type: [str] name of taper to apply to data weighting mask when
                                determining the fraction of data that are valid in a
                                candidate window
                            Supported string arguments:
                                'cosine':   apply a cosine taper of length
                                            wgt_taper_sec to each end of a
                                            data weighting mask
                                    aliases: 'cos', 'tukey'
                                'step':     set weights of samples in wgt_taper_sec of each
                                            end of a candidate window to 0, otherwise weights
                                            are 1 for unmasked values and 0 for masked values
                                    aliases: 'h', 'heaviside'
        :param index: [int] or [None] index value to assign to this window
                        see wyrm.structures.InstWindow
        :: OUTPUT ::
        :return window: [wyrm.structures.InstWindow] or [None]
                        If a candidate window is valid, this method returns a populated
                        InstWindow object, otherwise, it returns None

        """
        # branch basic compatability check
        if not isinstance(branch, dict):
            raise TypeError("branch must be type dict")
        # next_window_starttime basic compatability check
        if not isinstance(next_window_starttime, UTCDateTime):
            raise TypeError("next_window_starttimest be type obspy.UTCDateTime")
        # pad basic compatability check
        if not isinstance(pad, bool):
            raise TypeError("pad must be type bool")
        # extra_sec compatability checks
        if extra_sec is None:
            extra_sec = 0
        else:
            extra_sec = wcc.bounded_floatlike(
                extra_sec, name="extra_sec", minimum=0.0, maximum=self._window_sec
            )
        # wgt_taper_sec compatability checks
        if isinstance(wgt_taper_sec, str):
            if wgt_taper_sec.lower() == "blinding":
                wgt_taper_sec = self._blinding_sec
            else:
                raise SyntaxError(f'str input for wgt_taper_sec {wgt_taper_sec} not supported. Supported: "blinding"')
        else:
            wgt_taper_sec = wcc.bounded_floatlike(
                wgt_taper_sec,
                name="wgt_taper_sec",
                minimum=0.0,
                maximum=self._window_sec,
            )
        # wgt_taper_type compatability checks
        if not isinstance(wgt_taper_type, str):
            raise TypeError("wgt_taper_type must be type str")
        elif wgt_taper_type.lower() in ["cosine", "cos", "step", "heaviside", "h"]:
            wgt_taper_type = wgt_taper_type.lower()
        else:
            raise ValueError(
                'wgt_taper_type supported values: "cos", "cosine", "step", "heaviside", "h"'
            )

        # index compatability checks
        if index is None:
            pass
        else:
            index = wcc.bounded_intlike(
                index,
                name='index',
                minimum=0,
                maximum=None,
                inclusive=True
            )
        # Start of processing section #
        # Create a copy of the windowing attributes to pass to InstWindow()
        window_inputs = self.windowing_attr.copy()
        # Add target_starttime
        window_inputs.update({"target_starttime": next_window_starttime})

        # Calculate the expected end of the window
        next_window_endtime = next_window_starttime + self._window_sec
        # Compose kwarg dictionary for RtBuffTrace.get_trimmed_valid_fract()
        vfkwargs = {
            "starttime": next_window_starttime,
            "endtime": next_window_endtime,
            "wgt_taper_sec": wgt_taper_sec,
            "wgt_taper_type": wgt_taper_type,
        }
        # Iterate across component codes in branch
        for _k1 in branch.keys():
            # If _k1 is a Z component code
            if _k1 in self.code_map["Z"]:
                # Pull RtBuffTrace
                zbuff = branch[_k1]
                # Get windowed valid fraction
                valid_fract = zbuff.get_trimmed_valid_fraction(**vfkwargs)
                # Check valid_fraction
                if valid_fract >= self.tcf["Z"]:
                    # If sufficient data, trim a copy of the vertical data buffer
                    _tr = zbuff.to_trace()
                    _tr.trim(
                        starttime=next_window_starttime - extra_sec,
                        endtime=next_window_endtime + extra_sec,
                        pad=pad,
                        fill_value=None,
                    )
                    # Append to input holder
                    window_inputs.update({"Z": _tr})

            elif _k1 in self.code_map["N"]:
                hbuff = branch[_k1]
                valid_fract = hbuff.get_trimmed_valid_fraction(**vfkwargs)
                if valid_fract >= self.tcf["N"]:
                    # Convert a copy of the horizontal data buffer to trace
                    _tr = hbuff.to_trace()
                    # Trim data with option for extra_sec
                    _tr.trim(
                        starttime=next_window_starttime - extra_sec,
                        endtime=next_window_endtime + extra_sec,
                        pad=pad,
                        fill_value=None,
                    )
                    window_inputs.update({"N": _tr})

            elif _k1 in self.code_map["E"]:
                hbuff = branch[_k1]
                valid_fract = hbuff.get_trimmed_valid_fraction(**vfkwargs)
                if valid_fract >= self.tcf["E"]:
                    # Convert a copy of the horizontal data buffer to trace
                    _tr = hbuff.to_trace()
                    # Trim data with option for extra_sec
                    _tr.trim(
                        starttime=next_window_starttime - extra_sec,
                        endtime=next_window_endtime + extra_sec,
                        pad=pad,
                        fill_value=None,
                    )
                    window_inputs.update({"E": _tr})

        if "Z" in window_inputs.keys():
            output = InstrumentWindow(**window_inputs)
            
        else:
            output = None
        return output

    def _process_windows(
        self,
        tieredbuffer,
        extra_sec=None,
        pad=True,
        wgt_taper_sec="blinding",
        wgt_taper_type="cosine",
    ):
        """
        Iterates across all tier-0 keys (_k0) of a TieredBuffer object and
        assesses if each branch can produce a viable window, defined by having:
        1) Vertical component data specified by component codes in self.code_map['Z']
        2) Sufficient vertical data to satisfy the window size defined by the
            self.model.samping_rate and self.model.target_npts parameters and
            the z_valid_fract specified when initilalizing this WindWyrm. Additional
            settings are provide for downweighting window edge samples (see below)

        Additional data are ingested, if meeting comparable metrics,
        from horizontal channels if vertical channel data satisfy the
        requirements above.

        Successfully generated windowed data are copied into MLInstWindow objects
        and appended to this WindWyrm's queue with deque.leftappend()

        This method interacts with the WindWyrm.index dictionary, generating new
        entries for new instrument codes and initializing window starttime information

        For existing entries in WindWyrm.index, this method assesses if valid windows
        can be generated from a corresponding RtInstStream object data, generates
        windows in the event that one can be generated, and updates the index
        timing entry for when the next candidate window should start based on the
        overlap and sampling_rate specified in the SeisBench model associated
        with this WindWyrm.

        :: INPUTS ::
        :param rtinststream: [wyrm.structures.rtinststream.RtInstStream]
                            Realtime instrument stream object containing RtBuffTrace
                            objects
        -- kwargs passed to _branch2instwindow() --
            -> see its documentation for detailed descriptions of parameters
        :param extra_sec: [None] or [float] extra padding to place around
                            windowed data.
        :param pad: [bool] - allow padding when trimming
        :param wgt_taper_sec: [str] or [float] 'blinding' or float seconds
        :param wgt_taper_type: [str] 'cosine', 'step', or aliases thereof

        :: OUTPUT ::
        :return nnew: [int] number of new windows generated by this method
                        and added to the self.queue. Used as an early
                        termination criterion in self.pulse().
        """
        if not isinstance(tieredbuffer, TieredBuffer):
            raise TypeError(
                f"rtinststream must be type wyrm.structures.rtinststream.RtInstStream"
            )
        elif not isinstance(tieredbuffer.buff_class, TraceBuff):
            raise TypeError(f'TieredBuffer.buff_class must be TraceBuff. Found {tieredbuffer.buff_class}')

        nnew = 0
        for _k0 in tieredbuffer.keys():
            _branch = tieredbuffer[_k0]
            # If this branch does not exist in the WindWyrm.index
            if _k0 not in self.window_tracker.keys():
                self.window_tracker.update({_k0: deepcopy(self._index_template)})
                next_starttime = self.window_tracker[_k0]['next_starttime']

            # otherwise, alias matching index entry
            else:
                next_starttime = self.window_tracker[_k0]['next_starttime']

            # If this branch has data but index has the None next_starttime
            # Scrape vertical component data for a starttime
            if next_starttime == self.default_starttime:
                # Iterate across approved vertical component codes
                for _c in self.code_map["Z"]:
                    # If there is a match
                    if _c in _branch.keys():
                        # and the matching RtBuffTrace in the branch has data
                        if len(_branch[_c]) > 0:
                            # use the RtBuffTrace starttime to initialize the next_starttime
                            # in this branch of self.window_tracker
                            _first_ts = _branch[_c].stats.starttime
                            self.window_tracker[_k0].update({'next_starttime': _first_ts})
                            next_starttime = _first_ts
                            # and break
                            break

            # If the index has a UTCDateTime next_starttime
            if isinstance(next_starttime, UTCDateTime):
                # Cross reference vertical component timing with windowing
                _data_ts = None
                _data_te = None
                _data_max_length = None
                for _c in self.code_map['Z']:
                    if _c in _branch.keys():
                        if len(_branch[_c]) > 0:
                            _data_ts = _branch[_c].stats.starttime
                            _data_te = _branch[_c].stats.endtime
                            # _data_maxlength = _branch[_c].max_length
                            break
                # Normal operation cases (i.e., no gaps)
                # If next_starttime falls within the data timing
                if _data_ts <= next_starttime < _data_te:
                    # Proceed as normal
                    pass
                # Case where data has not buffered to the point of
                # starting to fill the next window
                elif next_starttime > _data_te:
                    # Calculate the size of the apparent data gap
                    gap_dt = next_starttime - _data_te
                    # If gap is less than two advance lengths, pass
                    if gap_dt < 2.*self._advance_sec:
                        # Proceed as normal
                        pass
                    # Otherwise trigger debugging/RuntimeError
                    else:
                        if self.debug:
                            print("RuntimeError('suspect a run-away next_starttime incremetation')")
                            breakpoint()
                        else:
                            RuntimeError('Suspect a run-away next_starttime incremetation - run in debug=True for diagnostics')

                ## PROCESSING DECISION NOTE ##
                # NOTE: This approach below for handling larger data gaps 
                # seeks to preserve integer values for the expected index of 
                # overlapping window times once a branch has been initialized. 
                #
                # This decision may result in some signal omission following
                # large data gaps that should not exceed one window in length. 

                # Case where RtBuffTrace may have experienced a gap large enough
                # to re-initialize a buffer.
                elif next_starttime < _data_ts:
                    gap_dt = _data_ts - next_starttime
                    # If gap_dt is less than the length of the buffer
                    if gap_dt < _data_max_length:
                        # Proceed as normal
                        pass
                    # Otherwise, determine how many integer window advances are needed
                    # To account for the gap by advancing windowin indices
                    else:
                        # Get number of advances needed to account for gap, rounded down
                        gap_nadv = gap_dt//self._advance_sec
                        # update next_starttime with integer number of advances in seconds
                        self.window_tracker[_k0]['next_starttime'] += gap_nadv*self._advance_sec
                        # update next_index by integer number of advances in counts
                        self.window_tracker[_k0]['next_index'] += gap_nadv
                        
                # Attempt to generate window from this branch
                window = self._branch2instwindow(
                    _branch,
                    self.window_tracker[_k0]['next_starttime'],
                    pad=pad,
                    extra_sec=extra_sec,
                    wgt_taper_sec=wgt_taper_sec,
                    wgt_taper_type=wgt_taper_type,
                    index=self.window_tracker[_k0]['next_index']
                )
                # if window is None:
                #     breakpoint()
                # If window is generated
                if window:
                    # Add InstWindow object to queue
                    self.queue.appendleft(window)
                    # update nnew index for iteration reporting
                    nnew += 1
                    # advance next_starttime by _advance_sec
                    self.window_tracker[_k0]['next_starttime'] += self._advance_sec
                    # advance next_index by 1
                    self.window_tracker[_k0]['next_index'] += 1

                # If window is not generated, go to next instrument
                else:
                    continue

        return nnew

    # ###################################### #
    # Updated Methods from Parent Class Wyrm #
    # ###################################### #

    def __str__(self):
        rstr = super().__str__()
        rstr += f" | Windows Queued: {len(self.queue)}\n"
        for _c in self.windowing_attr["target_order"]:
            rstr += f"  {_c}: map:{self.code_map[_c]} thresh: {self.tcf[_c]}\n"
        rstr += "ð -- Windowing Parameters -- ð"
        for _k, _v in self.windowing_attr.items():
            if isinstance(_v, float):
                rstr += f"\n  {_k}: {_v:.3f}"
            else:
                rstr += f"\n  {_k}: {_v}"
        return rstr

    def __repr__(self):
        rstr = self.__str__()
        return rstr


  
    def set_windowing_attr(
        self,
        fill_value=False,
        missing_component_rule=False,
        target_norm=False,
        target_sr=False,
        target_npts=False,
        target_channels=False,
        target_order=False,
        target_overlap=False,
        target_blinding=False,
        model_name=False,
    ):
        """
        Set (or update) attributes that specify the target dimensions
        of the windows being generated by this WindowWyrm
        """
        # Compatability check for fill_value
        if fill_value is None:
            self.windowing_attr.update({"fill_value": None})
        elif fill_value:
            _val = icc.bounded_floatlike(
                fill_value,
                name="fill_value",
                minimum=None,
                maximum=None,
                inclusive=True,
            )
            self.windowing_attr.update({"fill_value": _val})
        # Compatability check for missing_component_rule
        if not missing_component_rule:
            pass
        elif not isinstance(missing_component_rule, (str, type(None))):
            raise TypeError("missing_component_rule must be type str")
        elif isinstance(missing_component_rule, str):
            if missing_component_rule.lower() not in ["zeros", "clonez", "clonehz"]:
                emsg = (
                    f'missing_component_rule "{missing_component_rule}" not supported. '
                )
                emsg += (
                    f'Must be in "Zeros", "CloneZ", or "CloneHZ" (case insensitive).'
                )
                raise ValueError(emsg)
            else:
                self.windowing_attr.update(
                    {"missing_component_rule": missing_component_rule}
                )
        # Compatability check for target_norm
        if not target_norm:
            pass
        elif not isinstance(target_norm, (str, type(None))):
            raise TypeError("target_norm must be type str or None")
        elif isinstance(target_norm, str):
            if target_norm not in ["peak", "minmax", "std"]:
                raise ValueError(
                    f'target_norm {target_norm} not supported. Supported values: "peak", "minmax", "std"'
                )
            else:
                self.windowing_attr.update({"target_norm": target_norm})

        # Compatability check for target_sr
        if target_sr:
            _val = icc.bounded_floatlike(
                target_sr, name="target_sr", minimum=0, maximum=None, inclusive=False
            )
            self.windowing_attr.update({"target_sr": _val})
        # Compatability check for target_npts
        if target_npts:
            _val = icc.bounded_intlike(
                target_npts,
                name="target_npts",
                minimum=0,
                maximum=None,
                inclusive=False,
            )
            self.windowing_attr.update({"target_npts": _val})
        # Compatability check for target_channels
        if target_channels:
            _val = icc.bounded_intlike(
                target_channels,
                name="target_channels",
                minimum=1,
                maximum=6,
                inclusive=True,
            )
            self.windowing_attr.update({"target_channels": _val})
        # Compatability check for target_order:
        if not target_order:
            pass
        elif not isinstance(target_order, (str, type(None))):
            raise TypeError("target_order must be type str or None")
        elif isinstance(target_order, str):
            if target_order.upper() == target_order:
                if len(target_order) == self.windowing_attr["target_channels"]:
                    self.windowing_attr.update({"target_order": target_order})
                else:
                    raise ValueError(
                        "number of elements in target order must match target_channels"
                    )
            else:
                raise SyntaxError("target order must be all capital characters")

        # Compatability check for target_overlap_npts
        if target_overlap:
            _val = icc.bounded_intlike(
                target_overlap,
                name="target_overlap",
                minimum=-1,
                maximum=self.windowing_attr["target_npts"],
                inclusive=False,
            )
            self.windowing_attr.update({"target_overlap": _val})
        # Compatability check for target_blinding_npts
        if target_blinding:

            _val = icc.bounded_intlike(
                target_blinding,
                name="target_blinding",
                minimum=0,
                maximum=self.windowing_attr["target_npts"],
                inclusive=True,
            )
            self.windowing_attr.update({"target_blinding": _val})

        if not model_name:
            pass
        elif not isinstance(model_name, (str, type(None))):
            raise TypeError("model_name must be type str or None")
        elif isinstance(model_name, str):
            self.windowing_attr.update({"model_name": model_name})

        # UPDATE DERIVATIVE ATTRIBUTES
        # If all inputs are not None for window length in seconds, update
        if target_sr:
            # if npts and sr are not None - get window_sec
            if target_npts:
                self._window_sec = target_npts / target_sr
                _val = (1.0 - min(self.tcf["N"], self.tcf["E"])) * 0.5
                _val *= self._window_sec
                self.windowing_attr.update({"tolsec": _val})
                # if npts, sr, and overlap are not None - get advance_sec
                if target_overlap:
                    adv_npts = target_npts - target_overlap
                    self._advance_sec = adv_npts / target_sr
            # If blinding is specified, calculated seconds equivalent
            if target_blinding:
                self._blinding_sec = target_blinding / target_sr

        return self

    def set_windowing_from_seisbench(
        self,
        model=sbm.EQTransformer().from_pretrained("pnw"),
        code_remap={"Z": "3A", "N": "1B", "E": "2C"},
    ):
        """
        Populate/update attributes from a seisbench.model.WaveformModel type object
        for this WindowWyrm
        """
        # Run compatability checks with keys of self.code_map and self.tcf

        if not isinstance(model, sbm.WaveformModel):
            raise TypeError("model must be a seisbench.models.WaveformModel")
        else:
            code_map_check = all(
                _c in model.component_order for _c in self.code_map.keys()
            )
            tcf_check = all(_c in model.component_order for _c in self.tcf.keys())
            if not code_map_check:
                raise KeyError(
                    f"model.component_order {model.component_order} does not match self.code_map keys"
                )
            if not tcf_check:
                raise KeyError(
                    f"model.component_order {model.component_order} does not match self.tcf keys"
                )

            if "norm" not in dir(model):
                mnorm = None
            else:
                mnorm = model.norm

            if "in_channels" not in dir(model):
                mchan = len(model.component_order)
            else:
                mchan = model.in_channels

            if "_annotate_args" not in dir(model):
                mover = 0
                mblind = 0
            else:
                if "overlap" in model._annotate_args.keys():
                    mover = model._annotate_args["overlap"][-1]
                else:
                    mover = 0
                if "blinding" in model._annotate_args.keys():
                    mblind = model._annotate_args["blinding"][-1][0]
                else:
                    mblind = 0

            # Update window attributers
            self.set_windowing_attr(
                target_norm=mnorm,
                target_sr=model.sampling_rate,
                target_npts=model.in_samples,
                target_channels=mchan,
                target_order=model.component_order,
                target_overlap=mover,
                target_blinding=mblind,
                model_name=model.name,
            )
        return self