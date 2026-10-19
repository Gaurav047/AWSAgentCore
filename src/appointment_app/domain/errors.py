class AppointmentError(Exception):
    """Base class for domain errors. Messages are safe to show to end users and LLMs."""


class NotFoundError(AppointmentError):
    pass


class ValidationError(AppointmentError):
    pass


class SlotUnavailableError(AppointmentError):
    pass
