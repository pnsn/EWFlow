import logging, sys

class CriticalExitHandler(logging.Handler):
    def __init__(self, exit_code=1):
        super().__init__()
        if isinstance(exit_code, int):
            self.exit_code = exit_code
        else:
            raise ValueError('exit_code must be type int')

    def emit(self, record):
        if record.levelno == logging.CRITICAL:
            sys.exit(self.exit_code)

def rich_error_message(e):
    etype = type(e).__name__
    emsg = str(e)
    return f'{etype}: {emsg}'


def raise_error_to_log(logger, errortype, errormsg='', log_severity='DEBUG', exit_severity='CRITICAL', exit_code=1):
    if isinstance(logger, logging.Logger):
        pass
    else:
        raise TypeError(f'logger must be type logging.Logger. Not type {type(logger)}')
    if isinstance(errortype, str):
        if isinstance(eval('errortype', type)):
            pass
        else:
            raise ValueError('errortype must be the string-type name of a valid python *Error type. E.g., ValueError')
    else:
        raise TypeError('error type must be type str. E.g., "ValueError"')
    if isinstance(errormsg, str):
        pass
    else:
        raise TypeError('errormsg must be type str')
    
    if log_severity.upper() not in ['DEBUG','INFO','WARNING','ERROR','CRITICAL']:
        raise ValueError('log_severity {log_severity} not supported. Use: DEBUG, INFO, WARNING, ERROR, or CRITICAL')
    else:
        pass

    if exit_severity.upper() not in ['DEBUG','INFO','WARNING','ERROR','CRITICAL']:
        raise ValueError('exit_severity {exit_severity} not supported. Use: DEBUG, INFO, WARNING, ERROR, or CRITICAL')
    else:
        pass

    if isinstance(exit_code, int):
        pass
    else:
        raise ValueError('exit code must be type int')
    eval(f'logger.{log_severity.upper()}({errormsg})')
    

    
    