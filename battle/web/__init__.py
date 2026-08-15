"""Flask blueprint + frontend for the battle panel (M2).

Thin adapter over battle.core: every request loads the Encounter from
battle.json, delegates to core resolution, saves back. The file is the
single source of truth, shared with the CLI (the DM's channel).
"""
