# Scripts

One-off diagnostic and development scripts used while building this project,
kept for reference rather than deleted - they show the actual investigative
process (see [METHODOLOGY.md](../METHODOLOGY.md) and [SPEC.md](../SPEC.md)
for the narrative these scripts were part of).

**Not all of these still run as-is.** In particular, several
`test_write_flow*.py` scripts from the Phase 4 write investigation reference
functions (`build_control_payload_reversed`, `build_control_payload_bitfix`,
etc.) that were consolidated into a single correct `build_control_payload()`
once the write encoding was actually solved - see
`jebao_gizwits/control.py`'s module docstring for the final, working
version. These scripts are left in place as a record of the experiments
that led there, not as a maintained tool.

Scripts that do still work as documented:
- `print_schema.py` - pretty-print a datapoint schema
- `fetch_datapoint_schema.py` - fetch a fresh schema from the Gizwits API (needs `JEBAO_USER_TOKEN` in `.env`)
- `capture_token.py` - mitmproxy addon used to capture that token (see CLAUDE.md for the full how-to)
- `read_status_live.py`, `poll_status_live.py` - read/poll live pump status over LAN
