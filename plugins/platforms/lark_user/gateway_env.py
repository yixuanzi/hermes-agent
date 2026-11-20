"""lark_user authorization names (``PlatformEntry`` fields).

Access is the adapter's own YAML policy (``dm_policy`` / ``group_policy`` / ``allow_from`` /
``group_allow_from``), enforced at intake so an unauthorized sender never reaches the gateway (and
never gets a pairing code sent under the user's name). ``open`` needs ``LARK_USER_ALLOW_ALL_USERS``;
the empty ``own_policy_env`` names make the startup gate read the policies from YAML only.
"""

AUTHZ = {
    "allow_all_env": "LARK_USER_ALLOW_ALL_USERS",
    "own_policy_env": ("", ""),
    "allowlist_suppresses_pairing": True,
}
