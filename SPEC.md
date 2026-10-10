# TC: TornadoVM Compiler Reference

**Version 0.3, 2026-10-10.** Describes `compiler/main` at commit `3c0e33d76` of
`mikepapadim/tornadovm-compiler`. **Normative:** sections 2 to 10. **Informative:** section 1, section 11 and the appendices.

## Abstract

TC is the Graal-free compiler for TornadoVM. It translates Java bytecode of TornadoVM tasks into GPU
source code: CUDA C++ today, with OpenCL C and Metal Shading Language planned. This document is the
reference for:

- **TC-SSA**, the typed SSA intermediate representation.
- **TC-S**, the structured, non-SSA form the source emitters read.
- The intrinsics that carry TornadoVM's programming model.
- The compilation pipeline and every pass in it.
- The kernel ABI TC shares with the TornadoVM runtime.
- The diagnostics TC reports.

Section 11 lists every paper, specification and compiler TC borrows a concept from.

The document follows the conventions of the LLVM Language Reference:

- Each instruction has a *Syntax*, *Overview*, *Arguments*, *Semantics* and *Example* entry.
- **"must"** marks a rule the verifier enforces.
- **"is undefined"** is not used: TC-SSA has Java semantics everywhere, so there is no undefined
  behaviour and no `poison` (§4.1).

---

## 1. Introduction

### 1.1 Goals

1. **Java semantics, exactly.** Integer overflow, `/` and `%`, shift masking, float-to-int
   conversion, NaN and signed zero follow the Java Language Specification. They are properties of
   the instructions, not of a backend.
2. **Readable at every step.** Every stage has a text form with a printer and a parser. Values
   keep their Java names, and every instruction keeps its Java source line. The emitted C reads like
   hand-written code.
3. **One core, thin dialects.** Frontend, optimiser and structuring are target-independent. A
   backend is a capability record (`TargetCaps`), a dialect printer and a table of intrinsic
   spellings.
4. **Pure Java, no native compiler libraries.** TC runs on JDK 21+ without JNI or FFM bindings to
   LLVM, MLIR or NVVM. Those tools can still read TC's output as text, as separate processes.
5. **Parity first.** Every program the Graal path compiles for TornadoVM must compile under TC, with
   the same results and the same launch configuration. A differential harness checks this per test.

### 1.2 Two IRs

| Form | Module | Shape | Consumers |
|---|---|---|---|
| **TC-SSA** | `tc-ir` | typed SSA; basic blocks with **block arguments** (no phi nodes) | every analysis and optimisation pass |
| **TC-S** | `tc-lower` | structured statements (`if`, `loop`, `switch`, `break`), non-SSA variables, expression trees | the source emitters |

### 1.3 Pipeline at a glance

```
bytecode ─► frontend ─► inline ─► specialise ─► atomics ─► reduce ─► batch ─► parallel
        ─► optimize ─► local-arrays ─► abi ─► optimize.post-abi ─► structure (TC-S) ─► emit (CUDA C++)
```

Section 8 defines each stage and each pass.

---

## 2. Lexical structure and identifiers

- **Values:** `%` followed by a name (`%i`, `%sum`, `%a_data`) or a number (`%7`). Names come from
  the class file's local-variable table where one exists, and carry no semantics. The SSA versions
  of one Java local share its name (`%i`, `%i.1`).
- **Blocks:** `^` followed by a label (`^entry`, `^bci17`, `^head`). Frontend blocks are named after the bytecode
  index that starts them.
- **Functions:** `@` followed by `Owner.method`. Static callees get a descriptor-hash suffix when overloaded.
  Device-launched kernels are named `Owner.method_dp`.
- **Comments:** `;` to the end of the line.
- **Strings:** `"…"` with no escaped quote. Attribute strings use C escapes, and quotes inside them
  are written as octal escapes (`\042`).
- **Source locations:** `loc("File.java":line, bci=n)` after an instruction (optional).

---

## 3. Module structure

```
module      ::= function*
function    ::= ("kernel" | "device") "@" name "(" params ")" "->" type attrs? "{" block+ "}"
params      ::= (value ":" type) ("," value ":" type)*
block       ::= "^" label ("(" value ":" type ("," value ":" type)* ")")? attrs? ":" inst* terminator
inst        ::= (value "=")? opcode operands (":" type)? attrs? loc?
attrs       ::= "[" attr ("," attr)* "]"
attr        ::= ident ("(" text ")")?
```

### 3.1 Functions

- A **`kernel`** is an entry point launched by the host or from the device. Its return type must be
  `void`. Kernels are the only functions that may allocate `shared` memory.
- A **`device`** function is a callee. Kernels are compiled as one function: every call is inlined
  before the Tornado stages (§8.2), so `call` never reaches emission.
- The parameters of a function are the arguments of its **entry block**. They are not repeated on the
  `^entry` header.

### 3.2 Blocks and block arguments

A block is a list of instructions ending in exactly one **terminator**. Instead of phi nodes, a block
declares **arguments**, and every branch to it passes one value per argument:

```
  br ^head(%zero)
^head(%i: i32):
  ...
  br ^head(%next)
```

Rules (verifier):

1. Every block ends with exactly one terminator, and terminators appear nowhere else.
2. A successor must receive exactly as many values as its argument count, and they must have the
   same types.
3. Every value is defined once: as a function parameter, a block argument or an instruction result.
4. Every use must be dominated by its definition. A block argument dominates its own block.

Leaving SSA is therefore a matter of **edge copies**: each branch assigns its values to the target's
arguments as one parallel copy (§8.11).

---

## 4. Type system

| Type | Syntax | Description |
|---|---|---|
| Integer | `i1 i8 i16 i32 i64` | Two's complement. `i1` is a condition. Java `boolean`, `byte`, `short`, `char` and `int` are `i32` on the operand stack and narrow only in memory. `char` is unsigned (`zext`). |
| Floating point | `f16 bf16 f32 f64` | IEEE 754 binary16, bfloat16, binary32, binary64. `HalfFloat` is `f16`. |
| Vector | `vec<N x T>` | `N` lanes of `T`, as a value. TornadoVM `Float2–4`, `Double2–4`, `Int2–4`, `Short2–3`, `Byte3–4` and `Half2` are vectors, and so are tensor-core fragments. |
| Pointer | `ptr<global>` `ptr<shared>` `ptr<constant>` `ptr<private>` | Opaque byte address in an address space (§4.2). |
| Java array | `array<T>` | A Java array or TornadoVM native array **before ABI lowering** (§8.10). It has a header and a length. |
| Object | `obj<Name>` | A Java object argument, **frontend only**. It must be gone after ABI lowering. |
| Void | `void` | The result of instructions with no value. |

### 4.1 Semantics are Java's

TC-SSA has no undefined behaviour and no poison value:

- Integer arithmetic wraps.
- `div` and `rem` truncate toward zero, and `MIN / -1 == MIN`.
- Shift counts are masked to the width (`& 31`, `& 63`).
- Float-to-integer conversion saturates, with NaN giving 0.
- `min` and `max` follow `Math.min` and `Math.max`, including NaN and −0.0.

Division by zero is not folded: Java throws there, and a kernel cannot.

### 4.2 Address spaces

| Space | Meaning | NVPTX number | CUDA | OpenCL | MSL |
|---|---|---:|---|---|---|
| `global` | device memory, task arguments | 1 | (generic) | `__global` | `device` |
| `shared` | per work-group (`KernelContext` local arrays) | 3 | `__shared__` | `__local` | `threadgroup` |
| `constant` | read-only constant region | 4 | `__constant__` | `__constant` | `constant` |
| `private` | per thread (`new T[n]` in a kernel) | 5 | local array | `__private` | `thread` |

Kernel parameters do not alias each other (TornadoVM contract), and `private` allocations alias
nothing.

---

## 5. Instruction reference

Each entry gives its text form. Unless stated otherwise, an instruction's operands must have the type
given by its result type.

### 5.1 Constants

**`const`**
- Syntax: `%r = const <literal> : T`
- Literals:
  - integers in decimal
  - floats in decimal or hexadecimal floating form
  - `nan`, `inf`, `-inf`
- Semantics: the value, truncated to `T` for integers.
- Example: `%z = const 0.0 : f32`

### 5.2 Integer and floating-point binary operations

**`add sub mul div rem and or xor shl shr ushr`** (integers) and **`fadd fsub fmul fdiv frem`** (floats)

- Syntax: `%r = <op> %a, %b : T`
- Arguments: two operands of the same type `T`: an integer type for the first group, a float type
  for the second.
- Semantics:

  | Operation | Semantics (JLS) |
  |---|---|
  | `add`, `sub`, `mul` | wrap modulo 2^width |
  | `div`, `rem` | truncate toward zero; `rem` takes the sign of the dividend |
  | `shl`, `shr`, `ushr` | arithmetic and logical shifts; count masked to `width-1` |
  | `fadd` … `fdiv` | IEEE 754 round to nearest |
  | `frem` | Java `%` on floats (C `fmod`) |

- Attribute `contract` on a float operation allows the target compiler to form an FMA. It is only set
  when `tornado.enable.fma` is on.
- Example: `%s = fadd %x, %y : f32 [contract]`

### 5.3 Unary operations

- **`neg`** and **`not`** (integer), **`fneg`** (float).
- Syntax: `%r = neg %a : T`
- `fneg` flips the sign bit (`-0.0` from `0.0`).

### 5.4 Comparisons and selection

**`icmp.<pred>`**
- Syntax: `%c = icmp.<pred> %a, %b : i1`
- Predicates: `eq ne slt sle sgt sge ult ule ugt uge`.

**`fcmp.<pred>`**
- Syntax: `%c = fcmp.<pred> %a, %b : i1`
- Ordered predicates `oeq one olt ole ogt oge` are false if either operand is NaN.
- Unordered predicates `ueq une ult ule ugt uge` are true if either operand is NaN.
- `ord` and `uno` test whether the operands are NaN.
- Java's `<` is `olt`. Java's `!=` is `une`.

**`select`**
- Syntax: `%r = select %c, %t, %f : T`
- `%c` is `i1`. The result is `%t` if `%c` is true, otherwise `%f`. Both operands are evaluated.

### 5.5 Conversions

- Syntax: `%r = <op> %a : T`

| Op | From → to | Semantics |
|---|---|---|
| `sext`, `zext` | iN → iM, M > N | sign or zero extension |
| `trunc` | iN → iM, M < N | keep the low bits |
| `i2f` | int → float | signed, round to nearest |
| `f2i.sat` | float → int | **saturating**; NaN → 0 (JLS §5.1.3) |
| `fext`, `ftrunc` | float widths | IEEE conversion, round to nearest even |
| `bitcast` | same bit width | reinterpret the bits |

### 5.6 Java-level memory (before ABI lowering)

**`array.load`**
- Syntax: `%v = array.load %a[%i] : T`
- `%a` is an `array<E>`, and `%i` is `i32` or `i64`.
- `T` must be `E`, with two exceptions:
  - A byte array may be read as any type at a byte index (`ByteArray.getHalfFloat`).
  - Any array may be read as `vec<N x E>` (a packed access, `HalfFloatArray.getHalf2`).
- An `array.load` on a `ptr<shared|private>` reads a local array that reached a Java-level access
  through an inlined helper.
- There is no bounds check (TornadoVM contract).

**`array.store`**
- Syntax: `array.store %a[%i], %v`
- The same type rules apply to `%v`.

**`array.length`**
- Syntax: `%n = array.length %a : i32` (or `i64`).
- Reads the length from the array header.

**`field.load`, `field.store`**
- Syntax: `%v = field.load %o, "name" : T` and `field.store %o, "name", %v`.
- A field of an object argument. ABI lowering resolves it at the JVM field offset (§9).

These instructions must not appear after ABI lowering.

### 5.7 Pointers and memory (after ABI lowering)

**`ptr.offset`**
- Syntax: `%q = ptr.offset %p, %bytes : ptr<S>`
- Byte offset, `i64`. The address space is preserved.

**`load`**
- Syntax: `%v = load %p : T`
- A naturally aligned load of `T`.

**`store`**
- Syntax: `store %p, %v`

**`alloca.<space>`**
- Syntax: `%p = alloca.private T[N] : ptr<private>`, `%p = alloca.shared T[N] : ptr<shared>`
- `N` must be greater than 0.
- `shared` must only be allocated in a `kernel`.
- `private` arrays are zero-filled (Java `new T[n]`). `shared` arrays start undefined, as in CUDA
  and on the Graal path.

### 5.8 Calls

**`call`**
- Syntax: `%r = call @callee(%args…) : T`
- Calls a `device` function of the module. All calls are inlined before the Tornado stages.
  Recursion is rejected with `TC-E1009`.

### 5.9 Intrinsics

- Syntax: `%r = <family>.<name>(%args…) : T`, or `<family>.<name>(%args…)` for void.
- An intrinsic is an instruction whose meaning is defined by the intrinsic table (§6) rather than
  by a dedicated opcode.
- Intrinsics are **not pure** unless listed as pure. Pure intrinsics are the `tornado.vector.*` and
  `math.*` families, and DCE, GVN and LICM may move or merge only those.

### 5.10 Terminators

| Terminator | Syntax |
|---|---|
| Branch | `br ^b(%v…)` |
| Conditional | `cond_br %c, ^t(%v…), ^f(%v…)` |
| Switch | `switch %x [k: ^b(%v…), …] default ^d(%v…)` (keys are distinct `i64`) |
| Return | `ret` or `ret %v` |
| Unreachable | `unreachable` |

---

## 6. Intrinsic reference

Each intrinsic has one row per target dialect in the spelling table (`tc-codegen/Intrinsics`). A
spelling is a template over `$0…$n`, optionally backed by a helper function that is emitted once per
translation unit. Families:

### 6.1 Work-items and synchronisation

| Intrinsic | Result | Meaning |
|---|---|---|
| `tornado.thread_id.global.{x,y,z}()` | i32 | global id |
| `tornado.thread_id.local.{x,y,z}()` | i32 | id within the work-group |
| `tornado.group_id.{x,y,z}()` | i32 | work-group id |
| `tornado.group_size.local.{x,y,z}()` | i32 | work-group size |
| `tornado.global_size.{x,y,z}()` | i32 | total threads in the dimension |
| `tornado.group_count.{x,y,z}()` | i32 | number of work-groups |
| `tornado.barrier.local()`, `tornado.barrier.global()` | void | work-group barrier |

### 6.2 Atomics

- `tornado.atomic.{add,min,max,exchange,cas}(ptr, v…)`: an atomic read-modify-write at an element address.
- `tornado.atomic_int.{new,get,set,add_get,get_add,sub_get,get_sub}(slot…)`:
  `java.util.concurrent.atomic.AtomicInteger`, as a slot of the `_atomics` ABI region (§9).

### 6.3 Warp (SIMD group)

`tornado.simd.{sum,shuffle_down,broadcast_first,any,all,ballot}`. All lanes of the warp take part,
with the full mask.

### 6.4 Tensor cores (CUDA, sm_80+)

| Intrinsic | Meaning |
|---|---|
| `tornado.mma.fragment`, `fragment_int` | accumulator initialisation |
| `tornado.mma.load_a`, `load_b`, `.offset`, `_s8`, `.swizzled` | `ldmatrix` fragment loads from shared tiles |
| `tornado.mma.load_a.global`, `load_b.global` | fragment loads straight from a global `HalfFloatArray`: 32-bit half-pair loads; B is transposed with `movmatrix` |
| `tornado.mma.m16n8k16`, `m16n8k16_bf16`, `m16n8k32_s8`, `m16n8k32_e4m3`, `m16n8k32_e5m2` | `mma.sync` |
| `tornado.mma.store`, `store_int`, `store_b.swizzled` | accumulator store |

Fragments are `vec<4 x f32>`, `vec<4 x i32>` and `vec<2 x i32>`.

### 6.5 Asynchronous copies (CUDA, sm_80+)

| Intrinsic | Meaning |
|---|---|
| `tornado.async_copy.4.e<b>(tile, slot, src, i)` | `cp.async.ca` of 4 bytes into `tile[slot]`; the source element size is `b` (2 for `HalfFloatArray`, 1 for bytes) |
| `tornado.async_copy.16.e<b>(…)` | `cp.async.cg` of 16 bytes |
| `tornado.async_copy.commit()` | `cp.async.commit_group` |
| `tornado.async_copy.wait(n)` | `cp.async.wait_group n` |

### 6.6 Local memory and collections (frontend-level, removed before ABI)

| Intrinsic | Meaning |
|---|---|
| `tornado.local_array.<space>.<elem>(n)` | an allocation whose length is not constant yet; it becomes an `alloca` once specialisation folds `n` (§8.9) |
| `tornado.local_array.length(p)` | the length of a local array seen through a merge |
| `tornado.local_collection(storage, n, width)` | a TornadoVM vector collection allocated in the kernel (`new VectorFloat2(n)`); scalar-replaced after inlining (§8.2) |

### 6.7 Reductions

`tornado.reduce.<op>(slot, value)`, where `<op>` is `add`, `mul`, `min` or `max`. It is the
block-level tree reduction that `@Reduce` lowers to (§8.5).

### 6.8 Vectors (pure)

| Intrinsic | Meaning |
|---|---|
| `tornado.vector.make(l0, …)` | a vector from its lanes |
| `tornado.vector.lane.<k>(v)` | read lane `k` |
| `tornado.vector.insert.<k>(v, s)` | a copy of `v` with lane `k` replaced |
| `tornado.vector.store_param(param, v)` | a kernel's vector parameter changed by setters; lowered to lane stores (§8.10) |

### 6.9 Math (pure, Java semantics)

`math.{sqrt,rsqrt,exp,log,sin,cos,tan,tanh,atan2,pow,floor,ceil,abs,min,max,fma}`. Each follows
`java.lang.Math` / `TornadoMath`. `min` and `max` propagate NaN and order −0.0 before +0.0.

### 6.10 Device launch, printing and quantisation

| Intrinsic | Meaning |
|---|---|
| `tornado.device_launch.<mode>.<Owner>.<method>(gx, gy, gz, lx, ly, lz, args…)` | CUDA dynamic parallelism (`KernelContext.launch`). `mode` is `default`, `tail` or `fire_and_forget`. The child is compiled into the same module (§8.13). |
| `tornado.printf(args…) [format("…")]` | `Debug.printf`: every thread prints, prefixed with its global id |
| `tornado.dp4a(a, offA, b, offB, c)`, `tornado.dp4a.packed(a, b, c)` | signed int8 dot product accumulate (`__dp4a`) |

---

## 7. Attributes

| Attribute | On | Meaning |
|---|---|---|
| `parallel(iv=k)` | loop header | the header's block argument `k` is the induction variable of a `@Parallel` loop |
| `grid(dim=d)` | loop header | after parallel lowering: dimension `d` of the launch grid, with a grid-stride step |
| `contract` | float operation | FMA formation allowed |
| `format("…")` | `tornado.printf` | the C format string body |
| `loc(...)` | instruction | Java file, line and bytecode index |

---

## 8. Compilation pipeline and pass reference

The driver is `TcCompiler`. A **stage** is a named step whose IR can be dumped
(`-Dtornado.tc.dump=<stage>`). A **pass** implements `Pass.run(Function) → changed`.
`PassManager` runs passes in order and, with `verifyEach`, verifies after every pass. A verifier
failure after a pass is `TC-E9001`, naming the pass.

### 8.0 Stage order

| # | Stage | Content |
|---|---|---|
| 0 | `bytecode` | listing of the method (HTML report only) |
| 1 | `frontend` | bytecode → TC-SSA, transitive callees, inlining, local collections |
| 2 | `input` | the kernel as translated |
| 3 | `specialise` | value specialisation, then canonicalize and CFG simplification to a fixpoint, then DCE |
| 4 | `atomics` | AtomicInteger slots |
| 5 | `reduce` | `@Reduce` lowering |
| 6 | `batch` | batched-execution index offset |
| 7 | `parallel` | `@Parallel` lowering, with a safety net for unknown bounds (`TC-E3103`) |
| 8 | `optimize` | the standard pipeline (§8.8) plus the math peepholes |
| 9 | `local-arrays` | pending allocations become `alloca` |
| 10 | `abi` | Java-level operations → pointers and the kernel ABI |
| 11 | `optimize.post-abi` | the standard pipeline on the address arithmetic |
| 12 | `structure` | TC-SSA → TC-S |
| 13 | `emit` | TC-S → CUDA C++ |

### 8.1 Frontend (`tc-frontend`)

- **Input:** class bytes through a `BytecodeSource`: a class path, a class loader, or bytes in a test.
- **Class files** are read by TC's own reader (JVMS ch. 4), with no dependency on the runtime or Graal.
- **Control flow** comes from the bytecode. Basic blocks split at branch targets, and a block is named
  after the bytecode index (`bci`) that starts it.
- **SSA construction** is on the fly (Braun et al. 2013):
  - Locals are SSA variables keyed by slot and bytecode kind.
  - Reads in unsealed blocks create provisional block arguments. Sealing completes them.
  - Trivial arguments are removed at the end.
- **The operand stack** is simulated. Values carry Java names from the local-variable table.
- **API binding.** TornadoVM API calls map to intrinsics or value semantics:
  - `KernelContext`, the native arrays, `HalfFloat`, vector types, `AtomicInteger`, `TornadoMath`,
    `QuantizationUtils` and `Debug.printf` bind to intrinsics.
  - Vector objects are values. A setter rebinds every local holding the object.
- **Host knowledge** comes from the source when it is backed by loaded classes:
  - `static final DeviceKernel` fields resolve through their `SerializedLambda`.
  - Enum classes give their constants and primitive fields. Enum values are ordinals, and a field read
    is a `select` over the constants' values.
- **Refusals** are diagnostics with stable codes (§10):
  - exception handlers (`TC-E1001`)
  - unsupported bytecodes (`TC-E1002`)
  - object allocation (`TC-E1011`)
  - recursion (`TC-E1009`)

### 8.2 Inliner and local collections (`tc-opt/Inliner`, `tc-frontend/LocalCollections`)

- **Inline-all.** At each call the block is split, the callee's blocks are cloned with parameters
  substituted, and each `ret v` becomes a branch to the continuation block, whose argument replaces
  the call. More than 1024 inlinings means recursion: `TC-E1009`.
- **LocalCollections** runs after inlining. Reads of the `storage` and `numElements` fields of a
  `tornado.local_collection` become the private array and `n`. The collection object then disappears:
  scalar replacement of a non-escaping allocation. Any other use is `TC-E1011`.

### 8.3 ValueSpecialisation (`tc-tornado`)

Folds facts about the actual task arguments into the kernel. Each fact is per parameter:

| Fact | Effect |
|---|---|
| `Length` | array lengths become constants; in batch mode, the length of the chunk |
| `Scalar` | the value of a scalar argument |
| `Instance` | the object layout of an object argument, its final field values and nested array lengths |
| `Reduce` | marks the `@Reduce` output |
| `IndexOffset` | `batchNumber × batchSize` for batched execution |

The result is valid only for those facts, and the runtime recompiles when they change.

### 8.4 AtomicSlots

Each `AtomicInteger`, whether created in the kernel with a constant initial value or passed as an
argument, becomes a slot of the `_atomics` region. Slots are registered with the runtime
(`globalAtomics` and `globalAtomicsParameters`, keyed by the compiled method), as on the Graal path.
`AtomicInteger` parameters leave the signature.

### 8.5 ReductionLowering

A `@Parallel` loop accumulating into a `@Reduce` array is matched against
`out[k] = out[k] op value`, where `op` is `+`, `*`, `min` or `max`, one per loop and on every
iteration. Otherwise the result is `TC-E3204`.

The loop becomes one thread per element:

1. Each thread computes its value.
2. A block tree reduction runs in shared memory (`tornado.reduce.<op>`).
3. Thread 0 writes the group's partial to `out[group + 1]`.

The runtime launches the power-of-two prefix of the domain and combines the partials, as for Graal.

### 8.6 BatchOffset

Under `withBatch`, each chunk is compiled with `IndexOffset = n × batchSize`. Uses of the parallel
induction variable **as a value** become `i + offset`. Array indices and the loop's own test and step
stay chunk-local, because device buffers hold one chunk.

### 8.7 ParallelLoopLowering

A header marked `parallel(iv=k)` with `i = start; i < end; i += step`, where the step is
loop-invariant (`TC-E3201` otherwise), becomes a **grid-stride loop**:

```
i = start + gid * step;  while (i < end) { …; i += gsize * step; }
```

- The innermost parallel loop maps to `x`.
- At most three loops nest (`TC-E3202`).
- `<=` bounds are normalised.
- The pass records the launch **domain** `(dim, offset, step, upper)`, which the runtime uses to
  size the grid. If a bound is unknown at compile time, the domain cannot be built and the kernel is
  refused with `TC-E3103` rather than launched with one thread.

### 8.8 Standard optimisation pipeline (`PassManager.standard`)

`canonicalize → cfg-simplify → dce → unroll → canonicalize → cfg-simplify → gvn → licm → canonicalize → gvn → cfg-simplify → dce`

| Pass | Name | Summary |
|---|---|---|
| Canonicalizer | `canonicalize` | Constant folding with Java semantics. Exact algebraic identities only (`x + (-0.0)`, but not `x + 0.0`). `ptr + 0`. `trunc(sext x)` → x. Vector rules: inserts covering every lane become `vector.make`, and a lane read of a made vector or through other-lane inserts takes the value. Iterates to a fixpoint. |
| CfgSimplify | `cfg-simplify` | Constant branches, merging straight-line blocks, removing unreachable blocks. |
| DeadCodeElimination | `dce` | Removes unused pure instructions and dead block arguments. |
| FullLoopUnroll | `unroll` | Unrolls loops of at most 8 trips with constant start, step and bound, a header plus one body block, and no allocations or calls. `parallel` loops are never unrolled. |
| GlobalValueNumbering | `gvn` | Dominator-scoped value numbering of pure instructions, with commutative operands normalised. |
| LoopInvariantCodeMotion | `licm` | Pure instructions whose operands are defined outside a loop move to its single entry, innermost loops first. Memory reads are not moved. |
| MathPeepholes | `math-peepholes` | `1 / sqrt(x)` → `rsqrt(x)` when `tornado.enable.mathOptimizations` is on (the default). |

### 8.9 Local arrays

Pending allocations (`tornado.local_array.*`) whose length specialisation made constant become
`alloca.<space> T[N]`. A length of 0 allocates one element. A non-constant length is `TC-E1004`.

### 8.10 AbiLowering

Lowers TC-SSA from Java level to the target ABI (§9):

- **Array parameters** become `ptr<global>`. Element `i` is at
  `ptr.offset(ptr.offset(a, header), i × size)`. The data pointer `a + header` is a named value
  (`a_data`), so GVN shares it and LICM hoists it.
- **Object parameters:**
  - Fields are read at their JVM offsets.
  - A reference field is `obj + 8 × u32` (compressed relative offset).
  - Primitive field stores write to the device copy.
- **Vector parameters** point at their raw lanes, which are loaded at entry.
  `tornado.vector.store_param` writes them back.
- **Intrinsics** that take whole arrays receive data pointers. Device launches receive the raw
  pointer, because the child has the same ABI.
- **Dropped:** `KernelContext` and `AtomicInteger` parameters.

After this pass the verifier rejects any `array.*`, `field.*` or `obj<…>` (`TC-E2112`).

### 8.11 Structurizer (`tc-lower`)

Turns TC-SSA into TC-S:

1. **Out of SSA:** the block arguments of each edge are one parallel copy. Copies are sequenced with
   one temporary per cycle (Rideau, Serpette and Leroy 2008). A value passed straight to an argument's
   own variable needs no copy.
2. **Structuring** recurses over the dominator tree (Ramsey 2022):
   - A loop header becomes `loop { … }`.
   - A merge node becomes code after an `if`.
   - `break` and `continue` exit to labelled regions.
   - Children of a loop header outside the natural loop are placed after the loop (refinement N5).
   - An irreducible CFG is `TC-E3001`.
3. **Expression folding:** constants, allocation addresses and pure single-use values in the same
   block, with no intervening side effect, fold into expression trees.

### 8.12 CEmitter (`tc-codegen`)

Prints TC-S as CUDA C++:

- **Declarations:** locals of one type are declared together, with pointers and scalars on separate lines.
- **Names:** Java names are kept. Clashes with C keywords and CUDA built-ins get a `_` prefix. Derived
  values get role names (`gsize_x`, `a_data`, `i_off`).
- **Statements:** while loops, `if`/`else` with branch-only conditions inlined, and `// File.java:line`
  comments.
- **Prelude:** each helper is emitted once, after its dependencies, together with the required
  `#include`s (`cuda_fp16.h`, `cuda_bf16.h`).

### 8.13 Device-launch linking (`TcCompiler`)

Each kernel named in a `tornado.device_launch` is compiled once, with no facts, as
`__global__ <binary_name>_dp`. The translation unit then contains, in order:

1. shared helpers, once each
2. forward declarations of every child (launch cycles are legal)
3. the children
4. the parent

The runtime sees `<<<` and compiles with `-rdc` against `libcudadevrt`.

---

## 9. Kernel ABI (CUDA)

```
extern "C" __global__ void <Owner>_<method>(long long *_kernel_context, unsigned char *_constant_region,
                                            unsigned char *_local_region, int *_atomics, <parameters>)
```

1. **Four reserved regions** always come first, in this order. Slot 3 of `_kernel_context` enables
   device-launch reports (`--threadInfo`).
2. **Parameters** follow in Java order, named from the local-variable table:
   - primitives and `HalfFloat` by value
   - every array, native array, vector and object as `unsigned char *` (the data follows the array header)
3. Native-array header size comes from `TargetCaps.arrayHeaderBytes` (16 on the reference runtime).
   The length is read at offset 0.
4. **Object arguments** are laid out like the JVM object. Offsets come from the same lookup the runtime
   uses to copy the object to the device. Objects must have exactly their declared class.
5. **Shared memory** is `__shared__` declarations in the body, never `_local_region`.
6. **Launch domain:** the grid is sized from the domain the compiler reports (§8.7), or from the
   `WorkerGrid` for `KernelContext` kernels.

---

## 10. Diagnostics

A diagnostic is a stable code, a message, the offending instruction in context, the Java location and
a hint. Codes are never reused:

| Range | Area |
|---|---|
| `TC-E1xxx` | frontend |
| `TC-E2xxx` | IR, parser and verifier |
| `TC-E3xxx` | Tornado lowering and structuring |
| `TC-E4xxx` | target |
| `TC-E9xxx` | internal |

Each code belongs to one **category**, which decides the exception the runtime raises when fallback
to Graal is off:

| Category | Meaning | Exception (fallback off) |
|---|---|---|
| `INVALID_KERNEL` | the kernel cannot run on a GPU as written | `TornadoBailoutRuntimeException`; for E1002, E1004 and E2108, `TornadoInternalError`, as Graal throws |
| `UNSUPPORTED` | runnable, but not compiled by TC yet | `TornadoBailoutRuntimeException` |
| `BAILOUT` | TC gives up on this pattern | `TornadoBailoutRuntimeException` |
| `INTERNAL` | a TC bug | `TornadoInternalError` "please report"; with fallback on, always reported on stderr |

The registry (`ErrorCodes`) holds 49 codes. A unit test (`everyCodeIsRegistered`) fails if a code used in the sources has no
an entry.

---

## 11. Sources and lineage (informative)

TC is a **clean-room** implementation:

- No code, comments, class structure, identifiers or file layout are copied from any compiler.
- What TC takes from the works below is **published ideas**: algorithms from papers, formats and
  semantics from public specifications, and design concepts from public documentation.
- Graal is used only as a **black box**: its emitted kernels (`--printKernel`) and test results are
  observed through the differential harness.

### 11.1 Papers (algorithms implemented from the publication)

| TC component | Source |
|---|---|
| SSA construction (frontend) | M. Braun, S. Buchwald, S. Hack, R. Leißa, C. Mallon, A. Zwinkau. *Simple and Efficient Construction of Static Single Assignment Form.* CC 2013. |
| Dominator tree | K. D. Cooper, T. J. Harvey, K. Kennedy. *A Simple, Fast Dominance Algorithm.* Software Practice & Experience, 2001. |
| Loop forest | P. Havlak. *Nesting of Reducible and Irreducible Loops.* TOPLAS 1997. |
| Out-of-SSA translation | B. Boissinot, A. Darte, F. Rastello, B. Dupont de Dinechin, C. Guillon. *Revisiting Out-of-SSA Translation for Correctness, Code Quality, and Efficiency.* CGO 2009; V. C. Sreedhar, R. D. Ju, D. M. Gillies, V. Santhanam. *Translating Out of Static Single Assignment Form.* SAS 1999. |
| Parallel-copy sequencing | L. Rideau, B. P. Serpette, X. Leroy. *Tilting at Windmills with Coq: Formal Verification of a Compilation Algorithm for Parallel Moves.* JAR 2008. |
| Structuring | N. Ramsey. *Beyond Relooper: Recursive Translation of Unstructured Control Flow to Structured Control Flow.* ICFP 2022. |
| Value numbering (concept) | C. Click. *Global Code Motion / Global Value Numbering.* PLDI 1995. TC uses dominator-scoped hashing, not GCM. |
| Escape-free scalar replacement (concept) | The idea behind partial escape analysis: L. Stadler, T. Würthinger, H. Mössenböck. *Partial Escape Analysis and Scalar Replacement for Java.* CGO 2014. TC applies it only to the fixed shapes of vector values and local collections. |

### 11.2 Specifications (formats and semantics)

| Used for | Specification |
|---|---|
| Class files, bytecode semantics | *The Java Virtual Machine Specification*, SE 21, ch. 4 and 6 |
| Arithmetic, conversions, `Math` | *The Java Language Specification*, SE 21, §4.2, §5.1.3, §15.17–15.20; `java.lang.Math` API |
| IEEE semantics | IEEE 754-2019 |
| CUDA spelling, `__shared__`, dynamic parallelism, `__dp4a`, `half2` | NVIDIA *CUDA C++ Programming Guide* |
| `cp.async`, `ldmatrix`, `mma.sync`, `movmatrix`, `dp4a` | NVIDIA *PTX ISA* (versions 7.0+) |
| Planned dialects | Khronos *OpenCL C 3.0*; Apple *Metal Shading Language Specification* |
| Text interop (optional, external tools only) | LLVM *Language Reference*; MLIR `cf`, `scf`, `arith`, `memref` and `gpu` dialect documentation |

### 11.3 Compilers TC borrows concepts from

| Compiler | Concept TC borrows | What TC does differently |
|---|---|---|
| **LLVM** | type names `iN`/`fN`; `icmp`/`fcmp` predicate sets; `sext`/`zext`/`trunc`/`bitcast`; opaque pointers with NVPTX address-space numbering; pass manager with verify-each; `opt-bisect`-style pass limit; FileCheck and lit as the test style (re-implemented in `tc-check` from their public guides); `update_test_checks`-style check generation | no `poison`/`undef`; Java semantics on every op; block arguments instead of phis |
| **MLIR** | block arguments instead of phi nodes (`cf`); structured regions for emission (`scf` → TC-S); op families like dialects (`arith`, `math`, `gpu`); `-verify-diagnostics`-style expected-error tests | one flat namespace of families, not pluggable dialects |
| **Cranelift (CLIF)** | flat typed SSA with block parameters; text form with a parser; strict verifier; file-based tests | Java-level types (`array<T>`, `obj<…>`) before ABI lowering |
| **Swift SIL** | block arguments as the SSA merge form; high-level, language-semantics IR before lowering | — |
| **ILGPU** (.NET GPU compiler) | high-level array and field operations lowered late; mapping a host API to intrinsics; specialising on argument values | TC lowers through structured C source, not PTX |
| **Graal / TornadoVM Graal path** | **behavioural reference only**: the kernel ABI (four regions, header, parameter order) and launch-domain behaviour observed from `--printKernel` and runtime code; the list of TornadoVM phases, used to scope what TC must deliver (`phase-migration-matrix.md`); naming conventions visible in emitted kernels (`_dp` children, printf prefix) | no Sea of Nodes, no floating nodes, no frame states, guards or deoptimisation, no LIR; a structured emitter instead of a structurizer over LIR |
| **OpenJDK Babylon / HAT** | one C core with thin dialect layers (CUDA, OpenCL, Metal) as a design concept | no code reused; TC's dialects are tables plus a printer |
| **Sea of Nodes / `Simple`** | studied, **not adopted**: TC keeps instructions ordered in blocks for readable dumps and simple out-of-SSA | — |
| **Alive2** | planned translation validation of canonicaliser rules on exported `.ll` (external process) | — |

### 11.4 TC-specific decisions

These are TC's own design decisions, without a single source:

| Decision | Description |
|---|---|
| N1 | Java semantics encoded in the operations, and TornadoVM intrinsics as first-class operations with per-dialect spellings |
| N2 | Two-level types: `array<T>` and `field.*` until ABI lowering, then pointers; the verifier enforces the switch |
| N3 | Mutable instructions over immutable sealed-record opcodes, with exhaustive `switch` dispatch instead of visitors |
| N4 | Java semantics win where the Graal path differs, and the deviations are recorded as accepted differences |
| N5 | Structuring refinement: loop-exit code is placed after the loop, not nested in it |
| N6 | Expression folding at out-of-SSA for readable C |
| N7 | TC's own class-file reader |
| N8 | Vector objects as SSA values, with setters rebinding locals and parameter write-back |
| N9 | Device-launched kernels linked into one translation unit with forward declarations |
| N10 | Enum values as ordinals, with field reads as selects over host-reflected constants |

---

## Appendix A. Example: one kernel through the pipeline

Java (TornadoVM example `MatrixMultiplication2DV2`, 32×32 shared-memory tiles):

```java
float[] aSub = context.allocateFloatLocalArray(TS * TS);
...
for (int t = 0; t < numTiles; t++) {
    aSub[col * TS + row] = A.get(tiledCol * size + globalRow);
    context.localBarrier();
    for (int k = 0; k < TS; k++) sum += aSub[k * TS + row] * bSub[col * TS + k];
    context.globalBarrier();
}
```

TC-SSA at `input` (excerpt):

```
%row = tornado.thread_id.local.x() : i32  loc("MatrixMultiplication2DV2.java":55, bci=1)
%aSub = alloca.shared f32[1024] : ptr<shared>
%numTiles = div %size, %8 : i32
```

CUDA at `emit` (excerpt):

```cuda
__shared__ float aSub[1024];
while (t < 32) {
  aSub[col_off + row] = A_data[tiledCol * 1024 + globalRow];  // MatrixMultiplication2DV2.java:72
  __syncthreads();
  ...
}
```

`size = 1024` and `numTiles = 32` were folded by specialisation. The row offset `col * 32` was hoisted
by LICM. The output was verified on an RTX 4090 against the Java result (2.1 TFLOPS).

## Appendix B. Options

| Property | Effect |
|---|---|
| `tornado.compiler=tc` | select TC for CUDA tasks |
| `tornado.compiler.fallback` | compile refused kernels with Graal (default `true`) |
| `tornado.printKernel` | print emitted source |
| `tornado.tc.dump=all\|<stages>` | write IR per stage |
| `tornado.tc.dumpDir` | where dumps and reports are written |
| `tornado.tc.only` | dump only kernels whose name contains this text |
| `tornado.tc.report=html` | one HTML report per kernel |
| `tornado.tc.verify=each\|end` | verifier frequency |
| `tornado.tc.verbose` | one line per kernel |
| `tornado.tc.stats` | compile and refusal counts at exit |
| `tornado.tc.compileTimes` | per-task compile times; needs the profiler on |

Profiler keys: `TASK_COMPILE_TC_TIME` and `TOTAL_TC_COMPILE_TIME`.
