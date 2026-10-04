class ProductFoundryError(Exception):
    """Base for errors the CLI and API report without a traceback."""


class QuotaExhausted(ProductFoundryError):
    """Every LLM provider is out of quota. The run pauses; it has not failed."""


class StageOutputInvalid(ProductFoundryError):
    """A stage output or a checkpoint edit does not satisfy its schema."""


class InvalidTransition(ProductFoundryError):
    """The requested action is not allowed in the run's current status."""


class RunNotFound(ProductFoundryError):
    pass


class RunAlreadyExists(ProductFoundryError):
    pass
