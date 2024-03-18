import glob, obspy, os, sys
import seisbench.models as sbm
import wyrm.data.dictstream as ds
import wyrm.data.componentstream as cs
import wyrm.data.mltrace as mt
import wyrm.core.coordinate as coor
import wyrm.core.process as proc 

common_pt = ['stead','instance','iquique','lendb']
EQT = sbm.EQTransformer()
EQT_list = common_pt + ['pnw']
EQT_aliases = {'Z':'Z3','N':'N1','E':'E2'}
PN = sbm.PhaseNet()
PN_aliases = {'Z':'Z3','N':'N1','E':'E2'}
PN_list = common_pt + ['diting']
# TODO: Develop extension that mocks up Hydrophone (H)
# PB_aliases = {'Z':'Z3','1':'N1','2':'E2', 'H': 'H4'}
# PBE = sbm.PickBlue(base='eqtransformer')

# PBN = sbm.PickBlue(base='phasenet')

## (ADDITIONAL) DATA SAMPLING HYPERPARAMETERS ##
# reference_sampling_rate 
RSR = 100.
#reference_channel_fill_rule
RCFR= 'clone_ref'
# Reference component
RCOMP = 'Z'

# Initialize Standard Processing Elements
treat_gap_kwargs = {} # see ComponentStream.treat_gaps() and MLTrace.treat_gaps() for defaults
                      # Essentially, filter 1-45 Hz, linear detrend, resample to 100 sps
# Initialize main pre-processing MethodWyrm objects (these can be cloned for multiple tubes)

# For treating gappy data
mwyrm_gaps = proc.MethodWyrm(
    pclass=cs.ComponentStream,
    pmethod='treat_gaps',
    pkwargs={})

# For synchronizing temporal sampling and windowing
mwyrm_sync = proc.MethodWyrm(
    pclass=cs.ComponentStream,
    pmethod='sync_to_reference',
    pkwargs={})

# For filling data out to 3-C from non-3-C data (either missing channels, or 1C instruments)
mwyrm_fill = proc.MethodWyrm(
    pclass=cs.ComponentStream,
    pmethod='apply_fill_rule',
    pkwargs={'rule': RCFR, 'ref_component': RCOMP})

# Initialize model specific normalization MethodWyrms
mwyrm_normEQT = proc.MethodWyrm(
    pclass=cs.ComponentStream,
    pmethod='normalize_traces',
    pkwargs={'norm_type': 'peak'}
)

mwyrm_normPN = proc.MethodWyrm(
    pclass=cs.ComponentStream,
    pmethod='normalize_traces',
    pkwargs={'norm_type': 'std'}
)

# Initialize WindowWyrm elements
windwyrmEQT = proc.WindowWyrm(
    component_aliases=EQT_aliases,
    model_name='EQTransformer',
    reference_sampling_rate=RSR,
    reference_npts=6000,
    reference_overlap=1800,max_pulse_size=5)

windwyrmPN = proc.WindowWyrm(
    component_aliases=PN_aliases,
    model_name='PhaseNet',
    reference_sampling_rate=100.,
    reference_npts=3001,
    reference_overlap=900,
    max_pulse_size=10)

# Initialize PredictionWyrm elements
predwyrmEQT = proc.PredictionWyrm(
    model=EQT,
    weight_names=EQT_list,
    devicetype='mps',
    compiled=True,
    max_pulse_size=10000,
    debug=True)

# predwyrmPN = proc.PredictionWyrm(
#     model=PN,
#     weight_names=PN_list,
#     devicetype='mps',
#     compiled=True,
#     max_pulse_size=10000,
#     debug=True)

# Compose EQT processing TubeWyrm
tubewyrmEQT = coor.TubeWyrm(
    wyrm_dict= {'window': windwyrmEQT,
                'gaps': mwyrm_gaps.copy(),
                'sync': mwyrm_sync.copy(),
                'norm': mwyrm_normEQT,
                'fill': mwyrm_fill.copy(),
                'predict': predwyrmEQT})

# Copy/Update to create PhaseNet processing TubeWyrm
tubewyrmPN = tubewyrmEQT.copy().update({'window': windwyrmPN,
                                        'norm': mwyrm_normPN,
                                        'predict': predwyrmPN})

# Compose CanWyrm to host multiple processing lines
canwyrm = coor.CanWyrm(wyrm_dict={'EQTransformer': tubewyrmEQT,
                             'PhaseNet': tubewyrmPN},
                  wait_sec=0,
                  max_pulse_size=1,
                  debug=False)


DATA_ROOT = os.path.join('/Volumes','TheWall','PNSN_miniDB','data','waveforms')
event_dir_list = glob.glob(os.path.join(DATA_ROOT,'uw*'))


fstring = '{ID}_{t0}_{t1}_{sr}'

for _dir in event_dir_list:
    # Compose write-out for this event
    write_dir = os.path.join(_dir,'predictions')
    if not os.path.exists(write_dir):
        os.makedirs(write_dir)
    # load bulk as stream
    st = obspy.read(os.path.join(_dir, 'bulk.mseed'))
    # convert into a DictStream
    data_dictstream = DictStream(traces=st)
    # pulse canwyrm
    can_out = canwyrm.pulse(data_dictstream)
    # write contents of component streams to 
    for _k, _v in can_out.items():
        for _y in _v:
            # TODO: Implement wyrm.data.componentstream.ComponentStream.write_to_mseed()
            _y.write_to_mseed(path=write_dir, fstring=fstring)