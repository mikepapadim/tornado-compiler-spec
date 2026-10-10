# TC compiler specification

The reference for TC, the Graal-free TornadoVM compiler being built in `mikepapadim/tornadovm-compiler`
(branch `compiler/main`). It covers:

- the TC-SSA and TC-S intermediate representations
- the intrinsics
- the pipeline and every pass
- the kernel ABI
- the diagnostics
- the sources TC borrows concepts from

| File | Content |
|---|---|
| `SPEC.md` | the specification (source of truth) |
| `index.html` | `SPEC.md` rendered in the browser; rebuild with `python3 build.py` |
| `build.py` | builds `index.html` from `SPEC.md` |

Private. The document describes the compiler at the commit named in its header.
