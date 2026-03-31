import random


def get_random_number(min_val=None, max_val=None, is_int=True, seed=None):
    """
    Return a random number within a specified range.

    Parameters
    ----------
    min_val : int or float, optional
        Lower bound of the range (inclusive).
        Defaults to 0 for integers or 0.0 for floats.
    max_val : int or float, optional
        Upper bound of the range.
        Defaults to 100 for integers or 1.0 for floats.
        For integers the bound is inclusive; for floats the interval is [min_val, max_val).
    is_int : bool, optional
        True to return an integer, False to return a float. Defaults to True.
    seed : int or None, optional
        Seed for the random number generator. If None the global random state is used.

    Returns
    -------
    int or float
        A randomly generated number within the specified range.

    Raises
    ------
    ValueError
        If min_val is greater than max_val.
    """
    if seed is not None:
        random.seed(seed)

    if is_int:
        resolved_min = 0 if min_val is None else int(min_val)
        resolved_max = 100 if max_val is None else int(max_val)

        if resolved_min > resolved_max:
            raise ValueError(
                f"min_val ({resolved_min}) must be less than or equal to max_val ({resolved_max})."
            )

        return random.randint(resolved_min, resolved_max)
    else:
        resolved_min = 0.0 if min_val is None else float(min_val)
        resolved_max = 1.0 if max_val is None else float(max_val)

        if resolved_min > resolved_max:
            raise ValueError(
                f"min_val ({resolved_min}) must be less than or equal to max_val ({resolved_max})."
            )

        return random.uniform(resolved_min, resolved_max)