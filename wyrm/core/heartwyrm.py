"""
:module: wyrm.core.heartwyrm
:auth: Nathan T. Stevens
:email: ntsteven (at) uw.edu
:org: Pacific Northwest Seismic Network
:license: AGPL-3.0
:purpose:
    This module contains the class definition for HeartWyrm objects that
    encapsulate a PyEW.EWModule and 


:attribution:
    This module builds on the PyEarthworm (C) 2018 F. Hernandez interface
    between an Earthworm Message Transport system and Python distributed
    under an AGPL-3.0 license.

"""
import PyEW
from time import time
from threading import Thread
from wyrm.core.base_wyrms import TubeWyrm
import pandas as pd


class HeartWyrm(TubeWyrm):
    """
    This class encapsulates a PyEW.EWModule object and provides the `run`,
    `start` and `stop` class methods required for

    This class inherits the pulse method from wyrm.core.base_wyrms.TubeWyrm
    """

    def __init__(
        self, pulse_rate, DR_ID, MOD_ID, INST_ID, HB_PERIOD, debug=False, wyrm_list=[]
    ):
        """
        ChildClass of wyrm.core.base_wyrms.TubeWyrm

        Initialize a HeartWyrm object that contains the parameters neededto
        initialize an EWModule object assocaited with a running instance of
        Earthworm.

        The __init__ method populates the attributes necessary to initialize
        the EWModule object with a subsequent heartwyrm.initialize_module().

        :: INPUTS ::
        :param pulse_rate: [float] rate in seconds to wait between pulses
        :param DR_ID: [int-like] Identifier for default reference memory ring
        :param MOD_ID: [int-like] Module ID for this instace of Wyrms
        :param INST_ID: [int-like] Installation ID (Institution ID)
        :param HB_PERIOD: [float-like] Heartbeat reporting period in seconds
        :param debug: [BOOL] Run module in debug mode?
        :param wyrm_list: [list-like] iterable set of *wyrm objects
                            with sequentially compatable *wyrm.pulse(x)

        :: PUBLIC ATTRIBUTES ::
        :attrib module: False or [PyEW.EWModule] - Holds Module Object
        :attrib pulse_rate: [float] rate in seconds to wait between pulses
        :attrib connections: [pandas.DataFrame]
                            with columns 'Name' and 'Ring_ID' that provides
                            richer indexing and display of connections
                            made to the EWModule via EWModule.add_ring(RING_ID)
                            Updated using heartwyrm.add_connection(RING_ID)
        :attrib wyrm_list: [list] list of *wyrm objects
                            Inherited from TubeWyrm
        
        :: PRIVATE ATTRIBUTES ::
        :attrib _default_ring_id: [int] Saved DR_ID input
        :attrib _module_id: [int] Saved MOD_ID input
        :attrib _installation_id: [int] Saved INST_ID input
        :attrib _HBP: [float] Saved HB_PERIOD input
        :attrib _debug: [bool] Saved debug input
        """
        super().__init__(wyrm_list)
        # Public Attributes
        self.module = False
        self.pulse_rate = pulse_rate
        self.conn_info = pd.DataFrame(columns=["Name", "RING_ID"])

        # Private Attributes
        self._default_ring_id = int(DR_ID)
        self._module_id = int(MOD_ID)
        self._installation_id = int(INST_ID)
        self._HBP = HB_PERIOD
        self._debug = debug

        # Module run attributes
        # Threading - TODO - need to understand this better
        self._thread = Thread(target=self.run)
        self.runs = True

    def __repr__(self):
        # Start with TubeWyrm __repr__ 
        rstr = super().__repr__()
        # List Pulse Rate
        rstr += f"Pulse Rate: {self.pulse_rate:.4f} sec\n"
        # List Module Status and Parameters
        if isinstance(self.module, PyEW.EWModule):
            rstr += "Module: Initialized\n"
        else:
            rstr += "Module: NOT Initialized\n"
        rstr += f'MOD: {self._module_id}'
        rstr += f'DR: {self._default_ring_id}\n'
        rstr += f'INST: {self._installation_id}\n'
        rstr += f'HB: {self._HBP} sec\n'
        rstr == f'debug: {self._debug}\n'
        # List Connections
        rstr += "---- Connections ----\n"
        rstr += f"{self.conn_info}\n"
        rstr += "-------- END --------\n"
        return rstr

    def initialize_module(self):
        # Initialize PyEarthworm Module
        if not self.module:
            try:
                self.module = PyEW.EWModule(
                    self._default_ring_id,
                    self._module_id,
                    self._installation_id,
                    self._HBP,
                    debug=self._debug,
                )
            except RuntimeError:
                print("HeartWyrm: There is already a EWModule running!")
        elif isinstance(self.module, PyEW.EWModule):
            print("HeartWyrm: Module already initialized")
        else:
            print(
                f"HeartWyrm.module is type {type(self.module)}\
                   -- incompatable!!!"
            )
            raise RuntimeError

    def add_connection(self, RING_ID, RING_Name):
        """
        Add a connection between target ring and the initialized self.module
        and update the conn_info DataFrame.

        Method includes safety catches
        """
        # === RUN COMPATABILITY CHECKS ON INPUT VARIABLES === #
        # Enforce integer RING_ID type
        try:
            RING_ID = int(RING_ID)
        except ValueError:
            print(
                f"Invalid RING_ID type (wants int-like) -\
                   input type is {type(RING_ID)}"
            )

        # Warn on non-standard RING_Name types and convert to String
        if not isinstance(RING_Name, (int, float, str)):
            print(
                f"Warning, RING_Name is not type (int, float, str) -\
                   input type is {type(RING_Name)}"
            )
            print("Converting RING_Name to <type str>")
            RING_Name = str(RING_Name)

        # --- End Input Compatability Checks --- #

        # === RUN CHECKS ON MODULE === #
        # If the module is not already initialized, try to initialize module
        if not self.module:
            self.initialize_module()
        # If the module is already initialized, pass
        elif isinstance(self.module, PyEW.EWModule):
            pass
        # Otherwise, raise TypeError with message
        else:
            print(f"Module type {type(self.module)} is incompatable!")
            raise TypeError
        # --- End Checks on Module --- #

        # === MAIN BLOCK === #
        # Final safety check that self.module is an EWModule object
        if isinstance(self.module, PyEW.EWModule):
            # If there isn't already an established connection to a given ring
            if not any(self.conn_info.RING_ID == RING_ID):
                # create new connection
                self.module.add_connection(RING_ID)

                # If this is the first connection logged, populate conn_info
                if len(self.conn_info) == 0:
                    self.conn_info = pd.DataFrame(
                        {"Name": RING_Name, "RING_ID": RING_ID}, index=[0]
                    )

                # If this is not the first connection, append to conn_info
                elif len(self.conn_info) > 0:
                    new_conn_info = pd.DataFrame(
                        {"Name": RING_Name, "RING_ID": RING_ID},
                        index=[self.conn_info.index[-1] + 1],
                    )
                    self.conn_info = pd.concat(
                        [self.conn_info, new_conn_info], axis=0, ignore_index=False
                    )

            # If connection exists, notify and provide the connection IDX in the notification
            else:
                idx = self.conn_info[self.conn_info.RING_ID == RING_ID].index[0]
                print(
                    f"IDX {idx:d} -- connection to RING_ID {RING_ID:d}\
                       already established"
                )

        # This shouldn't happen, but if somehow we get here...
        else:
            print(f"Module type {type(self.module)} is incompatable!")
            raise RuntimeError

    ################################
    ### PULSE POLYMORPHIC METHOD ###
    ################################

    def pulse(self, conn_in, conn_out):
        """
        ~~~ Polymorphic Method ~~~
        Specifiy input and output ring connection indices
        
        NOTE: This is only designed for one input ring and one
              output ring at the moment. A broader abstraction
              would be to pass some defining object for specific
              elements of self.wyrm_list should get access to
              the self.module....

                    Pulse should have a "module" input for all instances
                    ... go back to base class....
                    

        :: INPUTS ::
        :param conn_in: [int] index of connection in self.conn_info to
                        use for data input from Earthworm
        :param conn_out [int] index of connection in self.conn_info to
                        use for data output from Earthworm
        
        """

    ######################################
    ### MODULE OPERATION CLASS METHODS ###
    ######################################
        
    def start(self):
        """
        Start Module Command
        """
        self._thread.start()

    def stop(self):
        """
        Stop Module Command
        """
        self.runs = False

    def run(self, conn_in, conn_out):
        """
        Module Execution Command
        """
        while self.runs:
            if self.module.mod_sta() is False:
                break
            time.sleep(self.pulse_rate)
            # Run Pulse Command inherited from TubeWyrm
            self.pulse(conn_in = conn_in, conn_out = conn_out)
        # Polite shut-down of module
        self.module.goodbye()
        print("Exiting HeartWyrm Instance")


## TODO: Need to clarify handing of input and output connections and passing them to 