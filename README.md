# TC compiler specification

The reference for TC, TornadoVM's own compiler (it replaced the vendored Graal compiler), built in
`mikepapadim/tornadovm-compiler` (branch `compiler/main`). The compiler's code and its README live
there (`tornado-compiler/`); the design documents, the record of the migration from Graal and its
measurements live here.

| File | Content |
|---|---|
| `SPEC.md` | the specification (source of truth): IRs, intrinsics, pipeline and passes, ABI, diagnostics, sources |
| `index.html` | `SPEC.md` rendered in the browser; rebuild with `python3 build.py` |
| `build.py` | builds `index.html` from `SPEC.md` |
| `compiler-replacement/` | design documents and the migration record (moved from `docs/compiler-replacement/` in the compiler repository) |

## Design documents (`compiler-replacement/`)

| Document | Content |
|---|---|
| [architecture.md](compiler-replacement/architecture.md), [ir-specification.md](compiler-replacement/ir-specification.md) | the IR in detail |
| [backend-contracts.md](compiler-replacement/backend-contracts.md) | the kernel ABI per target |
| [optimizations.md](compiler-replacement/optimizations.md) | the passes |
| [parity-matrix.md](compiler-replacement/parity-matrix.md) | test parity with the Graal path, per device |
| [cold-start.md](compiler-replacement/cold-start.md) | compile latency, Leyden and native image |
| [provenance.md](compiler-replacement/provenance.md) | clean-room policy, algorithm sources, novel decisions |
| [running.md](compiler-replacement/running.md), [testing.md](compiler-replacement/testing.md), [toolchain.md](compiler-replacement/toolchain.md) | running TC, its tests, optional external tools |
| [compatibility-contract.md](compiler-replacement/compatibility-contract.md), [jdk-matrix.md](compiler-replacement/jdk-matrix.md) | what TC keeps compatible; JDKs |
| [examples.md](compiler-replacement/examples.md), `c3/` | kernels through the pipeline, next to Graal's output |

## Migration record (`compiler-replacement/`)

[plan.md](compiler-replacement/plan.md) and [checkpoints.md](compiler-replacement/checkpoints.md) (phases and
gates), [design-review.md](compiler-replacement/design-review.md), [graal-removal.md](compiler-replacement/graal-removal.md),
[graal-dependency-map.md](compiler-replacement/graal-dependency-map.md), [METAL.md](compiler-replacement/METAL.md)
(the Metal hand-over and its results), the phase and pipeline studies, and the raw data: `baseline/`
(test and differential runs), `perf/` (benchmarks, jitLLM, nsys/ncu), `spikes/` (AOT, Babylon).

Private. The documents describe the compiler at the commits they name.
