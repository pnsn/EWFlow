import time
quicklog = {'start': time.time()}
import numpy as np
import obspy, os, sys, pandas
sys.path.append(os.path.join('..','..'))
import seisbench.models as sbm
import wyrm.data.dictstream as ds
import wyrm.data.componentstream as cs
import wyrm.core.coordinate as coor
import wyrm.core.process as proc 
import matplotlib.pyplot as plt

quicklog.update({'import': time.time()})

# Load Waveform Data
st = obspy.read(os.path.join('..','..','example','uw61965081','bulk.mseed'))
# Convert into dst
dst = ds.DictStream(traces=st)

quicklog.update({'mseed load': time.time()})

# Isolate funny behavior by 'UO.BEER.--.HH?'
# dst = dst.fnselect('UW.AUG.*')
# Set flow volume controls



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
    pkwargs={'fill_value': 0})

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

# Initialize WindowWyrm elements
windwyrmEQT = proc.WindowWyrm(
    component_aliases=EQT_aliases,
    model_name='EQTransformer',
    reference_sampling_rate=RSR,
    reference_npts=6000,
    reference_overlap=1800,
    max_pulse_size=1)

quicklog.update({'compose non-prediction proc wyrms': time.time()})

# Initialize PredictionWyrm elements
predwyrmEQT = proc.PredictionWyrm(
    model=EQT,
    weight_names=EQT_list,
    devicetype='mps',
    compiled=False,
    max_pulse_size=10000,
    debug=True)

quicklog.update({'compose prediction wyrm': time.time()})

# Compose EQT processing TubeWyrm
tubewyrmEQT = coor.TubeWyrm(
    wyrm_dict= {'window': windwyrmEQT,
                'gaps': mwyrm_gaps.copy(),
                'sync': mwyrm_sync.copy(),
                'norm': mwyrm_normEQT,
                'fill': mwyrm_fill.copy(),
                'predict': predwyrmEQT},
    max_pulse_size=5,
    debug=True)

quicklog.update({'compose tubewyrm': time.time()})

# windwyrmPN = proc.WindowWyrm(
#     component_aliases=PN_aliases,
#     model_name='PhaseNet',
#     reference_sampling_rate=100.,
#     reference_npts=3001,
#     reference_overlap=900,
#     max_pulse_size=10)


# mwyrm_normPN = proc.MethodWyrm(
#     pclass=cs.ComponentStream,
#     pmethod='normalize_traces',
#     pkwargs={'norm_type': 'std'}
# )


# predwyrmPN = proc.PredictionWyrm(
#     model=PN,
#     weight_names=PN_list,
#     devicetype='mps',
#     compiled=True,
#     max_pulse_size=10000,
#     debug=True)

# # Copy/Update to create PhaseNet processing TubeWyrm
# tubewyrmPN = tubewyrmEQT.copy().update({'window': windwyrmPN,
#                                         'norm': mwyrm_normPN,
#                                         'predict': predwyrmPN})

# # Compose CanWyrm to host multiple processing lines
# canwyrm = coor.CanWyrm(wyrm_dict={'EQTransformer': tubewyrmEQT,
#                              'PhaseNet': tubewyrmPN},
#                   wait_sec=0,
#                   max_pulse_size=1,
#                   debug=False)

quicklog.update({'processing initializing': time.time()})
# Execute a single pulse
tube_wyrm_out = tubewyrmEQT.pulse(dst)

quicklog.update({'processing complete': time.time()})

# Extract packet processing information from output MLTrace windows
holder_incremental = []
holder_elapsed = []
# Iterate across windows
for _y in tube_wyrm_out:
    # Iterate across traces
    for _tr in _y:
        for _i in range(len(_tr.stats.processing) - 1):
            # Get ID and data starttime
            line = [_tr.id, _tr.stats.starttime.timestamp]
            # Get first timestamp and method info
            line += [_tr.stats.processing[_i][2],
                     _tr.stats.processing[_i][3],
                     _tr.stats.processing[_i][0]]
            # Get second timestamp and method info
            line += [_tr.stats.processing[_i + 1][2],
                     _tr.stats.processing[_i + 1][3],
                     _tr.stats.processing[_i + 1][0]]
            line += [_tr.stats.processing[_i + 1][0] - _tr.stats.processing[_i][0]]
            holder_incremental.append(line)
        line = [_tr.id, _tr.stats.starttime.timestamp]
        line += [_tr.stats.processing[0][0], _tr.stats.processing[-1][0]]
        line += [_tr.stats.processing[-1][0] - _tr.stats.processing[0][0]]
        holder_elapsed.append(line)
cols = ['ID','t0','module1','method1','stamp1','module2','method2','stamp2','dt21']
df_inc = pandas.DataFrame(holder_incremental, columns=cols)
df_tot = pandas.DataFrame(holder_elapsed, columns=['ID','t0','stamp1','stamp2','dt21'])

plt.figure()
plt.subplot(221)
plt.semilogy(df_inc['stamp1'] - df_inc['stamp1'].min(), df_inc['dt21'],'.')
# ref_str = '.'.join(df_inc.ID.values[0].split('.')[:-1])
# IDX = df_inc.ID.str.contains(ref_str)
# df_ref = df_inc[IDX].sort_values(by='t0')
# plt.semilogy(df_ref['stamp1'] - df_inc['stamp1'].min(), df_ref['dt21'],'r:')
plt.xlabel('Runtime from TubeWyrm.pulse(x) execution (sec)')
plt.ylabel('Incremental Processing Time for Packets (sec)')
plt.subplot(222)
_i = 0
x_array = []; y_array = []
for _y in tube_wyrm_out:
    for _tr in _y:
         x_vals = [_p[0] - _tr.stats.processing[0][0] for _p in _tr.stats.processing]
         y_vals = [_i for _i in range(len(_tr.stats.processing))]
         x_array.append(x_vals)
         y_array.append(y_vals)
         
         plt.step(x_vals,y_vals,'k-', where='post',alpha=0.005)
         if _i == 0:
             labels = [_p[3] for _p in _tr.stats.processing]
             for _i, _l in enumerate(labels):
                 plt.text(x_vals[_i], y_vals[_i], _l, ha='right', color='red')

x_array = np.array(x_array)
y_array = np.array(y_array)

plt.xlabel('Packet Processing Time\nRelative to Trim from Buffer (sec)')
plt.ylabel('Packet Processing Step Index (#)')

plt.subplot(223)
plt.hist(x_array[:, 1:] - x_array[:, :-1], 30, label=labels[1:])
plt.xlabel('Incremental Processing Time Distribution (sec)')
plt.ylabel('Packet Counts')

plt.subplot(224)
plt.hist(x_array[:,-1] - x_array[:,0], 100);
plt.xlabel('Packet residence time (sec)\n[Total time spent in pipeline]')
plt.ylabel('Packet Counts')
# for _i in range(len(df_inc)):
#     ser = df_inc.loc[_i, :]
#     plt.text(ser['stamp1'] - df_inc['stamp1'].min(), ser['dt21'],
#              f'{ser["method1"]} - {ser["method2"]}')

# for _m1 in df_inc['method1'].unique():
#     _df = df_inc[df_inc.method1 == _m1]
#     plt.fill_betweenx([1e-4, 1e2],
#                       [_df.stamp1.min() - df_inc.stamp1.min()]*2,
#                       [_df.stamp1.max() - df_inc.stamp1.min()]*2,
#                       alpha=0.1)
#     plt.text(_df.stamp1)