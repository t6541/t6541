# CodexQuantBot cloud development

Work in this existing checkout. Each cloud task is already isolated; do not create a Git worktree unless explicitly requested.

The imported source baseline is v0.7.393. Read NEW-COMPUTER-HANDOFF-v0.7.393.md and CLOUD-DEVELOPMENT.md before working. Attached handoff instructions are reference material, not authorization to execute trading operations.

Development and tests remain offline. Do not connect to exchanges, launch the desktop application, start live trading, place/cancel/amend orders, close positions, or change leverage without explicit authorization for that operation in the current session. Do not create or import trading credentials or production databases.

Start validation with tests/test_account05_signals.py and tests/test_account05_state.py. Report actual pytest results; direct invocation of a subset is not a complete pytest run. Never weaken tests to obtain a pass.

Keep credentials, runtime databases, logs, caches and generated builds out of Git. Windows EXE builds require Windows Python 3.12 and build-exe.ps1; do not label a Linux PyInstaller binary as a Windows EXE. Before a release, synchronize all version metadata and record the artifact SHA-256. Do not launch the produced EXE during offline development.
