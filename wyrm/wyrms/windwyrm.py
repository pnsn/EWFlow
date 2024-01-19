from wyrm.wyrms.wyrm import Wyrm
from wyrm.structures.window import MLInstWindow
import wyrm.util.seisbench_model_params as smp
import wyrm.util.input_compatability_checks as icc
import seisbench.models as sbm
from obspy import UTCDateTime
from collections import deque
from copy import deepcopy


class WindWyrm(Wyrm):
    """
    The WindWyrm class contains a dictionary of window-generation
    metadata for coordinating slicing (copying) data from buffers
    within a RtInstStream object to initialize WindowMsg objects.

    WindowMsg objects are staged in a queue for subsequent
    pre-processing and ML prediction.

    Each WindWyrm instance is specific to an ML model type.

    TODO:
     - either obsolite substr or use it to filter rtinststream's
     first layer of keys.
    """

    def __init__(
        self,
        model=sbm.EQTransformer().from_pretrained("pnw"),
        tapsec=0.06,
        tolsec=0.03,
        z_valid_frac=0.95,
        Z_codes="Z3",
        h_valid_frac=0.8,
        N_codes="N1",
        E_codes="E2",
        window_fill_value=0.0,
        missing_component_rule="Zeros",
        max_pulse_size=20,
        debug=False,
    ):
        """
        Initialize a WindWyrm object

        :: INPUTS ::
        :param model: [seisbench.models.WaveformModel] model object to
                        scrape information from on windowing parameters
        :param tapsec: [float] length of cosine taper to apply to data
                        in seconds as part of data preprocessing
                        see MLInstWindow()
        :param tolsec: [float] amount of time difference between input
                        trace timings to MLInstWindow() that is acceptable
        :param z_valid_frac: [float]

        """
        Wyrm.__init__(self, max_pulse_size=max_pulse_size, debug=debug)


            
        if not isinstance(model, sbm.WaveformModel):
            raise TypeError("model must be a seisbench.models.WaveformModel")
        else:
            # Tag Model as an attribute
            self.model = model
            # Get window advance seconds
            self._advance_sec = self.get_window_advance_seconds()
        # Compatability check for tapsec
        tapsec = icc.bounded_floatlike(
            tapsec,
            name="tapsec",
            minimum=0,
            maximum=self.model.in_samples / self.model.sampling_rate,
            inclusive=True,
        )
        # Compatability check for tolsec
        tolsec = icc.bounded_floatlike(
            tolsec,
            name="tolsec",
            minimum=0,
            maximum=self.model.in_samples / self.model.sampling_rate,
            inclusive=True,
        )

        # Compatability check for window_fill_value
        window_fill_value = icc.bounded_floatlike(
            window_fill_value,
            name="window_fill_value",
            minimum=None,
            maximum=None,
            inclusive=False,
        )

        # Compatability check for missing_component_rule
        if isinstance(missing_component_rule, str):
            if missing_component_rule.lower() in ["zeros", "clonez", "clonehz"]:
                _missing_component_rule = missing_component_rule
            else:
                raise ValueError(
                    'missing_component_rule must be in: "Zeros", "CloneZ", "CloneHZ"'
                )
        else:
            raise TypeError("missing_component_rule must be type str")


        # Compatability check for z_valid_frac
        self._zvft = icc.bounded_floatlike(
            z_valid_frac, name="z_valid_frac", minimum=0, maximum=1
        )
        # Compatability check for h_valid_frac
        self._hvft = icc.bounded_floatlike(
            h_valid_frac, name="h_valid_frac", minimum=0, maximum=1
        )
        # Compatability check for Z_codes
        self._Z_codes = icc.iterable_characters_check(Z_codes, name="Z_codes")
        # Compatability check for N_codes
        self.N_codes = icc.iterable_characters_check(N_codes, name="N_codes")
        # Compatability check for E_codes
        self.E_codes = icc.iterable_characters_check(E_codes, name="E_codes")

        # Initialize default attributes
        self._windowing_args = {
            "fill_value": window_fill_value,
            "tolsec": tolsec,
            "tapsec": tapsec,
            "missing_component_rule": _missing_component_rule,
            "model": self.model,
        }
        self.index = {}
        self._template = {
            "next_starttime": None
        }  # NOTE: Must have a non-UTCDateTime default value
        self.queue = deque([])


    def _update_model(self, model):

        if not isinstance(model, sbm.WaveformModel):
            raise TypeError("model must be a seisbench.models.WaveformModel")
        else:
            # Tag Model as an attribute
            self.model = model
            # Update window advance seconds
            self._advance_sec = self.get_window_advance_seconds()
            # Update model in _windowing_args dict
            self._windowing_args.update({'model': model})

    def get_window_advance_seconds(self):
        npts = self.model.in_samples
        opts = smp.get_overlap(self.model)
        apts = npts - opts
        asec = apts / self.model.sampling_rate
        return asec

    def _branch2instwindow(self, data_branch={}, index_branch=self._template.copy(), pad=True, extra_sec=1.0):
        if not isinstance(data_branch, dict):
            raise TypeError
        if not isinstance(index_branch, dict):
            raise TypeError
        # Create a copy of the kwargs to pass to MLInstWindow()
        window_inputs = self._windowing_args.copy()
        # Add target_starttime
        window_inputs.update({"target_starttime": index_branch['next_starttime']})

        # Calculate the expected end of the window
        window_te = window_ts + (self.model.in_samples) / self.model.sampling_rate
        # Compose kwarg dictionary for RtBuffTrace.get_trimmed_valid_fract()
        valid_fraction_kwargs = {
            "starttime": window_ts,
            "endtime": window_te,
            "wgt_taper_sec": smp.get_blinding(self.model) / self.model.sampling_rate,
            "wgt_taper_type": "cosine",
        }
        # Iterate across component codes in branch
        for _k2 in data_branch.keys():
            # If _k2 is a Z component code
            if _k2 in self._Z_codes:
                # Pull RtBuffTrace
                zbuff = data_branch[_k2]
                # Get windowed valid fraction
                valid_fract = zbuff.get_trimmed_valid_fract(**valid_fraction_kwargs)
                # Check valid_fraction
                if valid_fract >= self._zvft:
                    # If sufficient data, trim a copy
                    _tr = zbuff.to_trace()
                    _tr.trim(
                        starttime=window_ts - extra_sec,
                        endtime=window_te + extra_sec,
                        pad=pad,
                        fill_value=None,
                    )
                    # Append to input holder
                    window_inputs.update({"Z": _tr})

            elif _k2 in self.N_codes + self.E_codes:
                hbuff = data_branch[_k2]
                valid_fract = hbuff.get_trimmed_valid_fract(**valid_fraction_kwargs)
                if valid_fract >= self._hvft:
                    _tr = hbuff.to_trace()
                    _tr.trim(
                        starttime=window_ts - extra_sec,
                        endtime=window_te + extra_sec,
                        pad=pad,
                        fill_value=None,
                    )
                    # Append to input holder
                    if _k2 in self.N_codes:
                        window_inputs.update({"N": _tr})
                    elif _k2 in self.E_codes:
                        window_inputs.update({"N": _tr})
        if "Z" in window_inputs.keys():
            output = MLInstWindow(**window_inputs)
        else:
            output = None
        return output

    def _process_windows(self, rtinststream, pad=True):
        nnew = 0
        for _k1 in rtinststream.keys():
            _branch = rtinststream[_k1]
            # If this branch does not exist in the WindWyrm.index
            if _k1 not in self.index.keys():
                self.index.update({_k1: {deepcopy(self._template)}})
                _idx = self.index[_k1]
            # otherwise, alias matching index entry
            else:
                _idx = self.index[_k1]

            # If this branch has data for the first time
            if _idx["next_starttime"] == self._template["next_starttime"]:
                # Iterate across approved vertical component codes
                for _c in self._Z_codes:
                    # If there is a match
                    if _c in _branch.keys():
                        # and the matching RtBuffTrace in the branch has data
                        if len(_branch[_c]) > 0:
                            # use the RtBuffTrace starttime to initialize the windowing index
                            _first_ts = _branch[_c].stats.starttime
                            _idx.update({"next_starttime": _first_ts})
                            # and break
                            break
            # Otherwise, if the index has a UTCDateTime starttime
            elif isinstance(_idx["next_starttime"], UTCDateTime):
                # Do the match to the vertical trace buffer again
                # Set initial None-Type values for window edges
                _data_ts = None
                for _c in self._Z_codes:
                    if _c in _branch.keys():
                        if len(_branch[_c]) > 0:
                            # Grab start and end times
                            _data_ts = _branch[_c].stats.startime
                            break
                # If vertical channel doesn't show up, warning and continue
                if _data_ts is None:
                    print(
                        f"Error retrieving starttime from {_k1} vertical: {_branch.keys()}"
                    )
                    continue
                # If data buffer starttime is before or at next_starttime
                elif _data_ts <= _idx["next_starttime"]:
                    pass
                # If update next_starttime if data buffer starttime is later
                # Simple treatment for a large data gap.
                elif _data_ts > _idx["next_starttime"]:
                    _idx.update({"next_starttime": _data_ts})
                # Attempt to generate window from this branch
                ts = _idx["next_starttime"]
                window = self._branch2instwindow(_branch, ts, pad=pad)
                # If window is generated
                if window:
                    # Add WindowMsg to queue
                    self.queue.appendleft(window)
                    # update nnew index for iteration reporting
                    nnew += 1
                    # advance next_starttime for this index by the advance
                    _idx["next_starttime"] += self._advance_sec
                # If window is not generated, go to next instrument
                else:
                    continue

        return nnew

    def pulse(self, x):
        """
        Conduct up to the specified number of iterations of
        self._process_windows on an input RtInstStream object
        and return access to this WindWyrm's queue attribute

        :: INPUT ::
        :param x: [wyrm.structure.stream.RtInstStream]

        :: OUTPUT ::
        :return y: [deque] deque of WindowMsg objects
                    loaded with the appendleft() method, so
                    the oldest messages can be removed with
                    the pop() method in a subsequent step
        """
        for _ in range(self.max_pulse_size):
            nnew = self._process_windows(x)
            if nnew == 0:
                break
        # Return y as access to WindowWyrm.queue attribute
        y = self.queue
        return y

    # def _assess_risbranch_windowing(self, risbranch, **kwargs):
    #     """
    #     Conduct assessment of window readiness and processing
    #     style for a given RtInstStream branch based on thresholds
    #     and 1C rules set for this WindWyrm

    #     :: INPUTS ::
    #     :param risbranch: [dict] of [RtBuffTrace] objects
    #             Realtime Instrument Stream branch
    #     :param **kwargs: key-word argment collector to pass
    #             to RtBuffTrace.get_window_stats()
    #                 starttime
    #                 endtime
    #                 pad
    #                 taper_sec
    #                 taper_type
    #                 vert_codes
    #                 hztl_codes

    #     :: OUTPUT ::
    #     :return pstats: [dict] dictionary with the following keyed fields
    #             '1C'     [bool]: Process as 1C data?
    #             'pcomp'  [list]: list of component codes to process starting
    #                              with the vertical component that have passed
    #                              validation.

    #         NOTE: {'1C': False, 'pcomp': False} indicates no valid window due to
    #                 absence of vertical component buffer
    #               {'1C': True, 'pcomp': False} indicates no valid window due to
    #                 insufficient data on the vertical
    #     """
    #     # Create holder for branch member stats
    #     bstats = {'vcode': False, 'nbuff': len(risbranch)}
    #     # Get individual buffer stats
    #     for k2 in risbranch.keys():
    #         # Get RtBuffTrace.get_window_stats()
    #         stats = risbranch[k2].get_window_stats(**kwargs)
    #         bstats.update({k2:stats})
    #         # Snag code
    #         if stats['comp_type'] == 'Vertical':
    #             bstats.update({'vcode': k2})
    #         elif stats['comp_type'] == 'Horizontal':
    #             if 'hcode' not in bstats.keys():
    #                 bstats.update({'hcodes':[k2]})
    #             else:
    #                 bstats['hcodes'].append(k2)

    #     # ### SENSE VERTICAL DATA PRESENT
    #     # TODO: a lot of this can get contracted out to WindowMsg!
    #     pstats = {}
    #     # if no vertical present, return bstats as-is
    #     if not bstats['vcode']:
    #         pstats.update({'1C': False, 'pcomp': False})
    #     # If vertical is present, proceed
    #     elif bstats['vcode']:
    #         # If there is not sufficient vertical data in the assessed window
    #         if bstats[bstats['vcode']]['percent_valid'] < self._zvft:
    #             pstats.update({'1C': True, 'pcomp': False})
    #         # If there is sufficient vertical data
    #         else:
    #             # If there is only a vertical, flag as ready, 1C
    #             if bstats['nbuff'] == 1:
    #                 pstats.update({'1C': True, 'pcomp': [bstats['vcode']]})
    #             # If there is at least one horizontal buffer
    #             elif bstats['nbuff'] == 2:
    #                 # If zero-pad or clone vertical 1c rule, flag as ready, 1C
    #                 if self.ch_fill_rule in ['zeros','cloneZ']:
    #                     pstats.update({'1C': True, 'pcomp': [bstats['vcode']]})
    #                 # If horizontal cloning
    #                 elif self.ch_fill_rule == 'cloneZH':
    #                     # If
    #                     if bstats[bstats['hcodes'][0]]['percent_valid'] < self.h1c_thresh:
    #                         pstats.update({'1C': True, 'pcomp': [bstats['vcode']]})
    #                     else:
    #                         pstats.update({'1C': False, 'pcomp': [bstats['vcode']] + bstats['hcodes']})
    #                 else:
    #                     raise ValueError(f'ch_fill_rule {self.ch_fill_rule} incompatable')
    #             # If there are three buffers
    #             elif bstats['nbuff'] == 3:
    #                 # If both horizontals have sufficient data flag as ready, 3C
    #                 if all(bstats[_c]['percent_valid'] >= self.h1c_thresh for _c in bstats['hcodes']):
    #                     pstats.update({'1C': False, 'pcomp': [bstats['vcode']] + bstats['hcodes']})
    #                 # If one horizontal has sufficient data
    #                 elif any(bstats[_c]['percent_valid'] >= self.h1c_thresh for _c in bstats['hcodes']):
    #                     pstats.update({'1C': False, 'pcomp': [bstats['vcode']]})
    #                     # If clone horizontal ch_fill_rule
    #                     if self.ch_fill_rule == 'cloneZH':
    #                         for _c in bstats['hcodes']:
    #                             if bstats[_c]['percent_valid'] >= self.h1c_thresh:
    #                                 pstats['pcomp'].append(_c)
    #                     else:
    #                         pstats.update({'1C': True})
    #                 # If no horizontal has sufficient data
    #                 else:
    #                     pstats.update({'1C': True, 'pcomp': [bstats['vcode']]})
    #     return pstats

    # def window_rtinststream(self, rtinststream, **kwargs):
    #     nsubmissions = 0
    #     for k1 in rtinststream.keys():
    #         # Alias risbranch
    #         _risbranch = rtinststream[k1]
    #         # Create new template in index if k1 is not present
    #         if k1 not in self.index.keys():
    #             self.index.update({k1:{deepcopy(self._template)}})
    #             _idxbranch = self.index[k1]
    #         else:
    #             _idxbranch = self.index[k1]
    #         # # # ASSESS IF NEW WINDOW CAN BE GENERATED # # #
    #         # If next_starttime is still template value
    #         if isinstance(_idxbranch['next_starttime'], type(None)):
    #             # Search for vertical component
    #             for _c in self._Z_codes:
    #                 if _c in _risbranch.keys():
    #                     # If vertical component has any data
    #                     if len(_risbranch[_c]) > 0:
    #                         # Assign `next_starttime` using buffer starttime
    #                         _buff_ts = _risbranch[_c].stats.starttime
    #                         _idxbranch['next_starttime'] = _buff_ts
    #                         # break iteration loop
    #                         break

    #         # Handle case if next_starttime already assigned
    #         if isinstance(_idxbranch['next_starttime'], UTCDateTime):
    #             ts = _idxbranch['next_starttime']
    #             te = ts + self.wsec

    #             pstats = self._assess_risbranch_windowing(
    #                 _risbranch,
    #                 starttime=ts,
    #                 endtime=te,
    #                 vert_codes=self._Z_codes,
    #                 hztl_codes=self.h_codes,
    #                 taper_type=self.ttype,
    #                 taper_sec=self.blindsec)

    #             # Update with pstats
    #             _idxbranch.update(pstats)

    #         # If flagged for windowing
    #         if isinstance(_idxbranch['pcomp'], list):
    #             _pcomp = _idxbranch['pcomp']
    #             # If not 3-C
    #             if len(_pcomp) <= 2:
    #                 # Get vertical buffer
    #                 _buff = _risbranch[_pcomp[0]]
    #                 _trZ = _buff.as_trace().trim(
    #                     starttime=ts,
    #                     endtime=te,
    #                     pad=True,
    #                     nearest_sample=True)
    #                 # If zero-padding rule
    #                 if self.ch_fill_rule == 'zeros':
    #                     _trH = _trZ.copy()
    #                     _trH.data *= 0
    #                 # If cloning rule
    #                 elif self.ch_fill_rule in ['cloneZ', 'cloneHZ']:
    #                     # If only vertical available, clone vertical
    #                     if len(_pcomp) == 1:
    #                         _trH = _trZ.copy()
    #                     # If horziontal available
    #                     elif len(_pcomp) == 2:
    #                         # For cloneZ, still clone vertical
    #                         if self.ch_fill_rule == 'cloneZ':
    #                             _trH = _trZ.copy()
    #                         # Otherwise, clone horizontal
    #                         else:
    #                             _buff = _risbranch[_pcomp[1]]
    #                             _trH = _buff.as_trace().trim(
    #                                 starttime=ts,
    #                                 endtime=te,
    #                                 pad=True,
    #                                 nearest_sample=True)
    #                 # Compose window dictionary
    #                 window ={'Z': _trZ,
    #                          'N': _trH,
    #                          'E': _trH}
    #             # If 3C
    #             elif len(_pcomp)==3:
    #                 window = {}
    #                 for _c,_a in zip(_pcomp, ['Z','N','E']):
    #                     _buff = _risbranch[_c]
    #                     _tr = _buff.as_trace().trim(
    #                         starttime=ts,
    #                         endtime=te,
    #                         pad=True,
    #                         nearest_sample=True)
    #                     window.update({_a:_tr})
    #             # Append processing metadata to window
    #             window.update(deepcopy(_idxbranch))

    #             # Append window to output queue
    #             self.queue.appendleft(window)

    #             # Advance next_window by stride seconds
    #             _idxbranch['next_starttime'] += self.ssec

    #             # Increase index for submitted
    #             nsubmissions += 1
    #     return nsubmissions

    # def pulse(self, x):
    #     """
    #     :: INPUT ::
    #     :param x: [RtInstStream] populated realtime instrument
    #                 stream object

    #     :: OUTPUT ::
    #     :return y: [deque]
    #     """
    #     for _ in self.maxiter:
    #         ns = self.window_rtinststream(x)
    #         if ns == 0:
    #             break
    #     y = self.queue
    #     return y
