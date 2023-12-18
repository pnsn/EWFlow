"""
:module: wyrm.util.PyEW_translate.py
:auth: Nathan T. Stevens
:email: ntsteven (at) uw.edu
:org: Pacific Northwest Seismic Network
:license: AGPL-3.0

:purpose:
    This module contains methods for translating betwee python objects in the PyEarthworm python-side environment
    and other commonly used formats in the python seismology environment. 
"""
import numpy as np
from obspy import UTCDateTime, Trace


def pyew_tracebuff2_to_trace(
    py_ew_tracebuff2,
    maskval=np.nan,
    fill_value=-999,
    dtype=None
):
    """
    Convert the output of an EWModule.get_wave() call into an Obspy Trace

    :: INPUTS ::
    :param py_ew_tracebuff2: [dictionary]
            Output dictionary from EWModule.get_wave(). This method uses the
            following elements:
                'data' - [numpy.ndarray] or [numpy.ma.Masked_Array] -> trace.data
                'station' - [str] - trace.stats.station
                'network' - [str] - trace.stats.network
                'channel' - [str] - trace.stats.channel
                'location' - [str] - trace.stats.location
                'startt' - [float] -> [UTCDateTime] = trace.stats.starttime
                'samprate' - [int] - trace.stats.sampling_rate
                'datatype' - [str] -> trace.data.dtype
    :param maskval: [numpy.nan] or float-like
            No-data value to convert into masked elements if present in
            py_ew_tracebuff['data']
    :param fill_val: [float-like]
            Option to overwrite the fill_value on masked arrays.
            Default = -999 (standard for numpy.ma.masked_array's)
    :param dtype: [None] or [numpy.float32-like]
            Option to overwrite datatype inherited from py_ew_tracebuff

    :: OUTPUT ::
    :return trace: [obspy.core.trace.Trace]
            Trace object
    """
    pet = py_ew_tracebuff2
    trace = Trace()
    _data = pet["data"]

    # Handle potential data gaps...
    if np.sum(_data == maskval) == 0:
        trace.data = pet["data"]
    # ...as a masked array if there are maskvals
    else:
        _data = np.ma.masked_equal(pet["data"], maskval)
        _data.fill_value = fill_value

    # Get assigned data-type from message
    _dtype = {"i2": np.int16, "i4": np.int32, "i8": np.int64, "s4": np.int32}
    _dtype = _dtype[pet["datatype"]]
    # If there is a user-specified dtype, try to apply it
    if dtype is not None:
        try:
            _data.astype(dtype)
        # ... failing to do so, default to pet datatype
        except TypeError:
            print(
                f"{dtype} is invalid for re-assigning data dtype. Defaulting to tracebuff assigned dtype"
            )
            _data.astype(_dtype)
    # Otherwise, use stated datatype from pet
    else:
        _data.astype(_dtype)
    # Attach the data to the trace
    trace.data = _data

    # Add SNCL metadata to trace
    for _k in ["station", "network", "channel", "location"]:
        trace.stats[_k] = pet[_k]

    # Add start-time
    trace.stats.starttime = UTCDateTime(pet["startt"])

    # Add sampling_rate
    trace.stats.sampling_rate = pet["samprate"]

    return trace


def trace_to_pyew_tracebuff2(trace, datatype=("s4", np.int32)):
    """
    Based on the PyEarthworm_Workshop 2.Interactive_PyEarthworm demo
    https://github.com/Fran89/PyEarthworm_Workshop/blob/master/2.Interactive_PyEarthworm/InteractivePyEarthworm.ipynb

    Convert an obspy trace into a pyew_tracebuff2 formatted message

    NOTE: This method currently assumes that the data have no gaps and are already in a
    buffer. In the event of gappy data (i.e., a trace with (masked) data), consider using
    trace.split() and submit each contiguous trace as a separate message

    TODO: Add datatype handling

    :: INPUT ::
    :param trace: [obspy.core.trace.Trace]
        Obspy Trace Object
    :param datatype: [2-tuple]
        2-tuple with [str] pyew_tracebuff2 data type
                        and
                     [class] dtype to apply to the data of `trace`
    :: OUTPUT ::
    :return tracebuff2_msg: [dict]
        Dictionary containing all necessary information to populate an Earthworm Tracebuff2 message
    """
    tracebuff2_msg = {
        "station": trace.stats.station,
        "network": trace.stats.network,
        "channel": trace.stats.channel,
        "location": trace.stats.location,
        "nsamp": trace.stats.npts,
        "samprate": trace.stats.sampling_rate,
        "startt": np.round(trace.stats.starttime.timestamp, decimals=2),
        "datatype": datatype[0],
        "data": trace.data.astype(datatype[1]),
    }
    return tracebuff2_msg


def stream_to_pyew_tracebuff2_list(stream, datatype=("s4", np.int32)):
    """
    Convenience wrapper for preparing a set of tracebuff2 messages
    from a stream

    :: INPUTS ::
    :param stream: [obspy.core.stream.Stream]
    :param datatype: [tuple] - see trace_to_pyew_tracebuff2()

    :: OUTPUT ::
    :return tracebuff2_messages: [list]
                List of tracebuff2 [dict] messages
    """
    # Create a copy of the stream
    st = stream.copy()
    # Split the stream to dispose of gappy data (if any)
    st = st.split()
    # Iterate across traces and compose messages
    tracebuff2_messages = []
    for _tr in st:
        tracebuff2_msg = trace_to_pyew_tracebuff2(_tr)
        tracebuff2_messages.append(tracebuff2_msg)

    return tracebuff2_messages


def format_pick2k_msg(
    modID,
    index,
    sncl,
    utct,
    amps=[0, 0, 0],
    phase_hint="",
    polarity="",
    quality=2,
    messageID=10,
    orgID=1,
):
    """
    Create a formatted string to submit as a TYPE_PICK2K message
    for Earthworm
    http://www.earthwormcentral.org/documentation4/PROGRAMMER/y2k-formats.html

    :: INPUTS ::
    :param modID: [int] Module ID (1-255)
    :param index: [int] Message index assigned by picker (0-9999)
    :param sncl: [4-tuple] Station Network Component Location codes
    :param utct: [obspy.core.utcdatetime.UTCDateTime] pick time
    :param amps: [3-list] amplitudes of 1st, 2nd, and 3rd peak after arrival time
    :param phase_hint: [str] phase type code
    :param polarity: [str] first motion code
    :param quality: [int] pick quality (0-4)
    :param messageID: [int] message-type ID
    :param orgID: [int] organization ID

    :: OUTPUT ::
    :return msg: [str] formatted message ending with a newline (\\n) character.
    """
    # Format UTCDateTime into appropriate string format
    tpcs = [int(x) for x in utct.format_arclink().split(",")]
    fsec = tpcs[-2] + (tpcs[-1] / 1e6)
    # Message-type, module, and organization ID
    msg = f"{messageID:-3d}{modID:-3d}{orgID:-3d}"
    # Message index and Site Network Component
    msg += f" {index:-4d} {sncl[0]:5s}{sncl[1]:2s}{sncl[2]:3s}"
    # Pick polarity, quality, and phase hint
    msg += f" {polarity:1s}{quality:1d}{phase_hint:2s}"
    # YYYYMMDD
    msg += f"{tpcs[0]:04d}{tpcs[1]:02d}{tpcs[2]:02d}"
    # HHmmss.ff
    msg += f"{tpcs[3]:02d}{tpcs[4]:02d}{fsec:05.2f}"
    # amplitudes and newline
    msg += f"{amps[0]:08d}{amps[1]:08d}{amps[2]:08d}\n"

    return msg