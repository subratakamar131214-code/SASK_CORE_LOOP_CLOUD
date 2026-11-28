"""SASK v2.0 full-lifecycle autonomy layer.

The v1.0 kernel (``verify.py``, ``scale_orchestrator.py``, ``novelty_vault.py``,
the sealed suites, and the out-of-band supervisor) is treated as a read-only
dependency. Everything here lives in the mutable outer ring and must degrade
gracefully when optional infrastructure (``cryptography``, ``psutil``, cloud
keys, network) is absent.

Module map (see ``docs/V2_AUTONOMY.md``):

- ``boundary``    zone policy, kernel manifest, OS/runtime write guard
- ``curriculum``  falsifiable goal queue, saturation, frontier curriculum
- ``oracles``     verification-harness synthesis and differential consensus
- ``abstraction`` lemma/macro extraction and multi-encoding reformulation
- ``knowledge``   encrypted compounding rule store (in-memory decryption)
- ``repair``      crash-to-patch synthesis, blacklisting, gated rollback
- ``feeds``       read-only allowlisted empirical ingestion
- ``compute``     portfolio budgeting and provider failover mesh
- ``supervisor``  out-of-band watchdog process (heartbeat, limits, rollback)
- ``evidence``    filing-ready quarantine evidence bundles
- ``hud``         read-only observer digest server
"""

from __future__ import annotations

__version__ = "2.0.0"
__all__ = ["__version__"]
