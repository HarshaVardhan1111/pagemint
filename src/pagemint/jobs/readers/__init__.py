"""Readers for hiring systems with public job feeds."""

from pagemint.jobs.readers import ashby, greenhouse, lever, recruitee, workday

READERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workday": workday,
    "recruitee": recruitee,
}
