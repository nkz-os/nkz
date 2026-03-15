# Ruff lint errors in entity_management_api.py

Ruff is the Python linter run in this repo (e.g. via lint-staged on commit). The following error types appear in `services/entity-manager/entity_management_api.py`. They are **pre-existing** (not introduced by the admin-panel fixes). Fixing them is optional tech debt.

| Code | Meaning | Where / fix |
|------|--------|-------------|
| **F401** | Unused import | Remove `subprocess`, `send_file`, `threading` from top imports if not used. |
| **E402** | Import not at top of file | Imports after line 139 (`common.auth_middleware`, `db_helper`, `task_queue`, `prometheus_client`) are deliberate (path setup / conditional). To satisfy ruff: move shared path setup to top and keep these imports, or add `# noqa: E402` on those lines. |
| **F841** | Variable assigned but never used | e.g. `parcel_id` (1540), `cur` (5130), `entity_type` (5742), `plan_level` (6186), `job` (6995), `last_pulled_at` (7724). Remove the assignment or use the variable. |
| **E722** | Bare `except:` | Use `except Exception:` (or a specific type) instead of `except:` for clarity. |
| **E701** | Multiple statements on one line | e.g. `if not val: return 0` and `except: return 0` — split to two lines. |

To list current errors:

```bash
ruff check services/entity-manager/entity_management_api.py
```

To auto-fix what ruff can:

```bash
ruff check --fix services/entity-manager/entity_management_api.py
```

Some fixes (e.g. E402, F841) may require manual edits. The commit hook may still pass if lint-staged is configured to allow a certain number of warnings.
