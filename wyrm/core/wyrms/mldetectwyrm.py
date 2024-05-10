"""
:module: wyrm.processing.mldetect
:auth: Nathan T. Stevens
:email: ntsteven (at) uw.edu
:org: Pacific Northwest Seismic Network
:license: AGPL-3.0

:purpose:
    This module hosts class definitions for a Wyrm class that operates PyTorch
    based earthquake body phase detector/labeler models.

    PredictWyrm - a submodule for runing predictions with a particular PyTorch/SeisBench model
                architecture with pretrained weight(s) on preprocessed waveform data
                    PULSE:
                        input: deque of preprocessed WindowStream objects
                        output: deque of MLTrace objects
"""

import time, torch, copy
import numpy as np
import seisbench.models as sbm
from collections import deque
from wyrm.core.trace.mltrace import MLTrace
from wyrm.core.stream.wyrmstream import WyrmStream
from wyrm.core.stream.windowstream import WindowStream
from wyrm.core.wyrms.wyrm import Wyrm


###################################################################################
# MLDETECT WYRM CLASS DEFINITION - FOR BATCHED PREDICTION IN A PULSED MANNER ####
###################################################################################
    
class MLDetectWyrm(Wyrm):
    """
    Conduct ML model predictions on preprocessed data ingested as a deque of
    WindowStream objects using one or more pretrained model weights. Following
    guidance on model application acceleration from SeisBench, an option to precompile
    models on the target device is included as a default option.

    This Wyrm's pulse() method accepts a deque of preprocessed WindowStream objects
    and outputs to another deque (self.queue) of MLTrace objects that contain
    windowed predictions, source-metadata, and fold values that are the sum of the
    input data fold vectors
        i.e., data with all 3 data channels has predictions with fold = 3 for all elements, 
              whereas data with both horizontal channels missing produce predictions with 
              fold = 1 for all elements. Consequently data with gaps may have fold values
              ranging \in [0, 3]

    This functionality allows tracking of information density across the prediction stage
    of a processing pipeline.
    """


    def __init__(
        self,
        model=sbm.EQTransformer(),
        weight_names=['pnw',
                      'instance',
                      'stead'],
        devicetype='cpu',
        compiled=True,
        max_pulse_size=1000,
        timestamp=False,
        debug=False):
        """
        Initialize a PredictionWyrm object

        :: INPUTS ::
        :param model: [seisbench.models.WaveformModel] child class object
        :param weight_names: [list-like] of [str] names of pretrained model
                        weights included in the model.list_pretrained() output
                        NOTE: This object holds distinct, in-memory instances of
                            all model-weight combinations, allowing rapid cycling
                            across weights and storage of pre-compiled models
        :param devicetype: [str] name of a device compliant with a torch.device()
                            object and the particular hardware of the system running
                            this instance of Wyrm 
                                (e.g., on Apple M1/2 'mps' becomes an option)
        :param compiled: [bool] should the model(s) be precompiled on initialization
                            using the torch.compile() method?
                        NOTE: This is suggested in the SeisBench documentation as
                            a way to accelerate model application
        :param max_pulse_size: [int] - maximum BATCH SIZE for windowed data
                            to pass to the model(s) for a single call of self.pulse()
        :debug: [bool] should this wyrm be run in debug mode?

        """
        super().__init__(timestamp=timestamp, max_pulse_size=max_pulse_size, debug=debug)
        
        # model compatability checks
        if not isinstance(model, sbm.WaveformModel):
            raise TypeError('model must be a seisbench.models.WaveformModel object')
        elif model.name == 'WaveformModel':
            raise TypeError('model must be a child-class of the seisbench.models.WaveformModel class')
        else:
            self.model = model
        
        # Model weight_names compatability checks
        # pretrained_list = model.list_pretrained() # This error catch is now handled with the preload/precopile setp
        if isinstance(weight_names, str):
            weight_names = [weight_names]
        elif isinstance(weight_names, (list, tuple)):
            if not all(isinstance(_n, str) for _n in weight_names):
                raise TypeError('not all listed weight_names are type str')
        # else:
        #     for _n in weight_names:
        #         if _n not in pretrained_list:
        #             raise ValueError(f'weight_name {_n} is not a valid pretrained model weight_name for {model}')
        self.weight_names = weight_names

        # device compatability checks
        if not isinstance(devicetype, str):
            raise TypeError('devicetype must be type str')
        else:
            try:
                device = torch.device(devicetype)
            except RuntimeError:
                raise RuntimeError(f'devicetype {devicetype} is an invalid device string for PyTorch')
            try:
                self.model.to(device)
            except RuntimeError:
                raise RuntimeError(f'device type {devicetype} is unavailable on this installation')
            self.device = device
        
        # Preload/precompile model-weight combinations
        if isinstance(compiled, bool):    
            self.compiled = compiled
        else:
            raise TypeError(f'"compiled" type {type(compiled)} not supported. Must be type bool')

        self.cmods = {}
        for wname in self.weight_names:
            if self.debug:
                print(f'Loading {self.model.name} - {wname}')
            cmod = self.model.from_pretrained(wname)
            if compiled:
                if self.debug:
                    print(f'...pre compiling model on device type "{self.device.type}"')
                cmod = torch.compile(cmod.to(self.device))
            else:
                cmod = cmod.to(self.device)
            self.cmods.update({wname: cmod})
        # Initialize output deque
        self.queue = deque()

    def __str__(self):
        rstr = f'wyrm.core.process.PredictWyrm('
        rstr += f'model=sbm.{self.model.name}, weight_names={self.weight_names}, '
        rstr += f'devicetype={self.device.type}, compiled={self.compiled}, '
        rstr += f'max_pulse_size={self.max_pulse_size}, debug={self.debug})'
        return rstr

    def pulse(self, x):
        """
        Execute a pulse on input deque of WindowStream objects `x`, predicting
        values for each model-weight-window combination and outputting individual
        predicted value traces as MLTrace objects in the self.queue attribute

        :: INPUT ::
        :param x: [deque] of [wyrm.core.WyrmStream.WindowStream] objects
                    objects must be 
        

        TODO: Eventually, have the predictions overwrite the windowed data
              values of the ingested WindowStream objects so predictions
              largely work as a in-place change
        """
        if not isinstance(x, deque):
            raise TypeError('input "x" must be type deque')
        
        qlen = len(x)
        # Initialize batch collectors for this pulse
        batch_data = []
        batch_fold = []
        batch_meta = []

        for _i in range(self.max_pulse_size):
            if len(x) == 0:
                break
            if _i == qlen:
                break
            else:
                _x = x.popleft()
                if not(isinstance(_x, WindowStream)):
                    x.append(_x)
                # Check that WindowStream is ready to split out, copy, and be eliminated
                if _x.ready_to_burn(self.model):
                    # Part out copied data, metadata, and fold objects
                    _data = _x.to_npy_tensor(self.model).copy()
                    _fold = _x.collapse_fold().copy() 
                    _meta = _x.stats.copy()
                    # Attach processing information for split
                    # _meta.processing.append([time.time(),
                    #                          'Wyrm 0.0.0',
                    #                          'PredictionWyrm',
                    #                          'split_for_ml',
                    #                          '<internal>'])
                    if self._timestamp:
                        _meta.processing.append(['PredictionWyrm','split_for_ml',str(_i), time.time()])
                    # Delete source WindowStream object to clean up memory
                    del _x
                    # Append copied (meta)data to collectors
                    batch_data.append(_data)
                    batch_fold.append(_fold)
                    batch_meta.append(_meta)
                # TODO: If not ready to burn, kick error
                else:
                    breakpoint()
                    raise ValueError('WindowStream is not sufficiently preprocessed - suspect an error earlier in the tube')
       
        # IF there are windows to process
        if len(batch_meta) > 0:
            # Concatenate batch_data tensor list into a single tensor
            batch_data = torch.Tensor(np.array(batch_data))
            batch_dst_dict = {_i: WyrmStream() for _i in range(len(batch_meta))}
            # Iterate across preloaded (and precompiled) models
            for wname, weighted_model in self.cmods.items():
                if self._timestamp:
                    batch_meta = batch_meta.copy()
                    for _meta in batch_meta:
                        _meta.processing.append(['PredictionWyrm','pulse','batch_start',time.time()])
                # Run batch prediction for a given weighted_model weight
                if batch_data.ndim != 3:
                    breakpoint()
                batch_pred = self.run_prediction(weighted_model, batch_data, batch_meta)
                # Reassociate window metadata to predicted values and send MLTraces to queue
                self.batch2dst_dict(wname, batch_pred, batch_fold, batch_meta, batch_dst_dict)
            # Provide access to queue as pulse output
            for _v in batch_dst_dict.values():
                self.queue.append(_v)

        # alias self.queue to output
        y = self.queue
        return y

    def run_prediction(self, weighted_model, batch_data, reshape_output=True):
        """
        Run a prediction on an input batch of windowed data using a specified model on
        self.device. Provides checks that batch_data is on self.device and an option to
        enforce a uniform shape of batch_preds and batch_data.

        :: INPUT ::
        :param weighted_model: [seisbench.models.WaveformModel] initialized model object with
                        pretrained weights loaded (and potentialy precompiled) with
                        which this prediction will be conducted
        :param batch_data: [numpy.ndarray] or [torch.Tensor] data array with scaling
                        appropriate to the input layer of `model` 
        :reshape_output: [bool] if batch_preds has a different shape from batch_data
                        should batch_preds be reshaped to match?
        :: OUTPUT ::
        :return batch_preds: [torch.Tensor] prediction outputs 
        """
        # Ensure input data is a torch.tensor
        if not isinstance(batch_data, (torch.Tensor, np.ndarray)):
            raise TypeError('batch_data must be type torch.Tensor or numpy.ndarray')
        elif isinstance(batch_data, np.ndarray):
            batch_data = torch.Tensor(batch_data)

        # RUN PREDICTION, ensuring data is on self.device
        if batch_data.device.type != self.device.type:
            batch_preds = weighted_model(batch_data.to(self.device))
        else:
            batch_preds = weighted_model(batch_data)

        # If operating on EQTransformer
        nwind = batch_data.shape[0]
        nlbl = len(self.model.labels)
        nsmp = self.model.in_samples
        if self.model.name == 'EQTransformer':
            detached_batch_preds= np.full(shape=(nwind, nlbl, nsmp), fill_value=np.nan, dtype=np.float32)
            for _l, _p in enumerate(batch_preds):
                if _p.device.type != 'cpu': 
                    detached_batch_preds[:, _l, :] = _p.detach().cpu().numpy()
                else:
                    detached_batch_preds[:, _l, :] = _p.detach().numpy()
        elif self.model.name == 'PhaseNet':
            if batch_preds.device.type != 'cpu':
                detached_batch_preds = batch_preds.detach().cpu().numpy() 
            else:
                detached_batch_preds = batch_preds.detach().numpy()
        else:
            raise NotImplementedError(f'model "{self.model.name}" prediction initial unpacking not yet implemented')
        # breakpoint()
        # # Check if output predictions are presented as some list-like of torch.Tensors
        # if isinstance(batch_preds, (tuple, list)):
        #     # If so, convert into a torch.Tensor
        #     if all(isinstance(_p, torch.Tensor) for _p in batch_preds):
        #         batch_preds = torch.concat(batch_preds)
        #     else:
        #         raise TypeError('not all elements of preds is type torch.Tensor')
        # # # If reshaping to batch_data.shape is desired, check if it is required.
        # # if reshape_output and batch_preds.shape != batch_data.shape:
        # #     batch_preds = batch_preds.reshape(batch_data.shape)

        return detached_batch_preds

    def batch2dst_dict(self, weight_name, batch_preds, batch_fold, batch_meta, dst_dict):
        """
        Reassociated batched predictions, batched metadata, and model metadata to generate MLTrace objects
        that are appended to the output deque (self.queue). The following MLTrace ID elements are updated
            component = 1st letter of the model label (e.g., "Detection" from EQTranformer -> "D")
            model = model name
            weight = pretrained weight name
        

        :: INPUTS ::
        :param weight_name: [str] name of the pretrained model weight used
        :param batch_preds: [torch.Tensor] predicted values with expected axis assignments:
                                axis 0: window # - corresponding to the axis 0 values in batch_fold and batch_meta
                                axis 1: label - label assignments from the model architecture used
                                axis 2: values
        :param batch_fold: [list] of [numpy.ndarray] vectors of summed input data fold for each input window
        :param batch_meta: [list] of [wyrm.core.WyrmStream.WindowStreamStats] objects corresponding to
                                input data for each prediction window
        :param dst_dict
        
        :: OUTPUT ::
        None

        :: ATTR UPDATE ::
        :attr queue: [deque] MLTrace objects generated as shown below are appended to `queue`
                    for window _i and predicted value label _j
                       mlt = MLTrace(data=batch_pred[_i, _j, :], fold = batch_fold[_i, :], header=batch_meta[_i])

        """

        # # Detach prediction array and convert to numpy
        # if batch_preds.device.type != 'cpu':
        #     batch_preds = batch_preds.detach().cpu().numpy()
        # else:
        #     batch_preds = batch_preds.detach().numpy()
        
        # Reshape sanity check
        if batch_preds.ndim != 3:
            if batch_preds.shape[0] != len(batch_meta):
                batch_preds = batch_preds.reshape((len(batch_meta), -1, self.model.in_samples))

        # TODO - change metadata propagation to take procesing from component stream, but still keep
        # timing and whatnot from reference_streams
        # Iterate across metadata dictionaries
        for _i, _meta in enumerate(batch_meta):
            # Split reference code into components
            # breakpoint()
            n,s,l,c,m,w = _meta.common_id.split('.')
            # Generate new MLTrace header for this set of predictions
            _header = {'starttime': _meta.reference_starttime,
                      'sampling_rate': _meta.reference_sampling_rate,
                      'network': n,
                      'station': s,
                      'location': l,
                      'channel': c,
                      'model': m,
                      'weight': weight_name,
                      'processing': copy.deepcopy(_meta.processing)}
            # Update processing information to timestamp completion of batch prediction
     
            # _header['processing'].append([time.time(),
            #                               'Wyrm 0.0.0',
            #                               'PredictionWyrm',
            #                               'batch2dst_dict',
            #                               '<internal>'])
            # Iterate across prediction labels
            for _j, label in enumerate(self.cmods[weight_name].labels):
                # Compose output trace from prediction values, input data fold, and header data
                _mlt = MLTrace(data = batch_preds[_i, _j, :], fold=batch_fold[_i], header=_header)
                # Update component labeling
                _mlt.set_comp(label)
                if self._timestamp:
                    _mlt.stats.processing.append(['PredictionWyrm','batch2dst',f'{_i+1} of {len(batch_meta)}',time.time()])
                # Append to window-indexed dictionary of WyrmStream objects
                if _i not in dst_dict.keys():
                    dst_dict.update({_i, WyrmStream()})
                dst_dict[_i].__add__(_mlt, key_attr='id')
                # Add mltrace to dsbuffer (subsequent buffering to happen in the next step)
                
