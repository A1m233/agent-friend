from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules


repo_root = Path(SPECPATH).parents[2]
entry = repo_root / "scripts" / "package" / "runtime_entry.py"

datas = []
binaries = []
hiddenimports = []

for package in (
    "agent",
    "agent_bridge",
    "bilibili_api",
    "llm_providers",
    "mcp",
    "memory",
    "runtime_config",
    "voice_bridge",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for package in (
    "keyring.backends",
    "qqbot_agent_sdk",
    "sqlite_vec",
    "tiktoken",
    "uvicorn",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

hiddenimports += collect_submodules("keyring.backends")
hiddenimports += collect_submodules(
    "litellm",
    filter=lambda name: not name.startswith(
        (
            "litellm.enterprise",
            "litellm.integrations",
            "litellm.proxy",
            "litellm.rag",
        )
    ),
)
hiddenimports += collect_submodules("tiktoken_ext")
datas += collect_data_files("certifi")
datas += collect_data_files(
    "litellm",
    includes=[
        "model_prices_and_context_window_backup.json",
        "policy_templates_backup.json",
        "provider_endpoints_support_backup.json",
    ],
)

analysis = Analysis(
    [str(entry)],
    pathex=[str(repo_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "agent_eval",
        "chromadb",
        "huggingface_hub",
        "memory_eval",
        "onnxruntime.quantization",
        "onnxruntime.tools",
        "onnxruntime.transformers",
        "pytest",
        "scipy",
        "sentence_transformers",
        "sklearn",
        "tools",
        "torch",
        "transformers",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="agent-friend-runtime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="agent-friend-runtime",
)
