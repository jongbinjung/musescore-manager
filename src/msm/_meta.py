"""Meta utilities used within the package"""

import inspect

from msm.exceptions import MissingDependencyError


def get_calling_module_name():
    # inspect.stack()[1] gets the frame record of the caller
    # .f_globals gets the global namespace of that frame
    # ['__name__'] retrieves the module name from that namespace
    return inspect.stack()[1].frame.f_globals["__name__"]


class RequiresExtra:
    """Context manager for importing optional dependencies with helpful error messages"""

    def __init__(self, name: str):
        """Initialize the RequiresExtra context manager

        Args:
            name: name of the extra that would provide the missing dependency;
                used in the error message in case import(s) fail

        """
        self.name = name

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            if issubclass(exc_type, ModuleNotFoundError):
                module_name = get_calling_module_name()
                msgs = [
                    str(exc_value),
                    f"\n{module_name} module requires the '{self.name}' extra. Update your dependencies, for example:",
                    f"\n\tuv add 'musescore-manager[{self.name}]'",
                ]
                raise MissingDependencyError("\n".join(msgs)) from exc_value
            return False
        return True
