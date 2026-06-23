import logging

class StreamlitLogHandler(logging.Handler):
    """
    Custom logging handler that buffers log records in memory
    so they can be rendered in the Streamlit user interface.
    """
    def __init__(self):
        super().__init__()
        self.logs = []
        
    def emit(self, record):
        log_entry = self.format(record)
        self.logs.append(log_entry)
        # Cap at 1000 items to prevent memory issues
        if len(self.logs) > 1000:
            self.logs.pop(0)

# Create singleton handler instance
_LOG_HANDLER = StreamlitLogHandler()
_LOG_HANDLER.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))

def get_logger():
    """Returns the application logger configured with the custom Streamlit handler."""
    logger = logging.getLogger("FacultyResumeParser")
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if called multiple times
    if not any(isinstance(h, StreamlitLogHandler) for h in logger.handlers):
        logger.addHandler(_LOG_HANDLER)
        
    # Also log to standard output for debugging
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, StreamlitLogHandler) for h in logger.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s'))
        logger.addHandler(sh)
        
    return logger

def get_buffered_logs():
    """Retrieves all buffered log statements for UI rendering."""
    return _LOG_HANDLER.logs

def clear_buffered_logs():
    """Clears all buffered log statements."""
    _LOG_HANDLER.logs.clear()
