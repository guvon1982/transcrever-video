"""Localiza as DLLs da NVIDIA (cuBLAS e cuDNN) instaladas via pip.

No Windows o faster-whisper não acha essas DLLs sozinho: os pacotes
`nvidia-cublas-cu12` e `nvidia-cudnn-cu12` as instalam dentro do .venv, e
cabe ao app avisar o Windows onde elas estão antes de carregar o modelo.
"""

import importlib.util
import os
from pathlib import Path


def cuda_dll_dirs() -> list[Path]:
    """Pastas `bin` dos pacotes `nvidia-*` instalados (vazio se não houver)."""
    spec = importlib.util.find_spec("nvidia")
    if spec is None or spec.submodule_search_locations is None:
        return []
    dirs: list[Path] = []
    for base in spec.submodule_search_locations:
        dirs.extend(sorted(path for path in Path(base).glob("*/bin") if path.is_dir()))
    return dirs


def setup_cuda_dll_paths() -> list[Path]:
    """Registra as pastas de DLL no Windows. Devolve as pastas registradas."""
    dirs = cuda_dll_dirs()
    if not dirs:
        return []
    if hasattr(os, "add_dll_directory"):  # só existe no Windows
        for directory in dirs:
            os.add_dll_directory(str(directory))
    # O ctranslate2 carrega as DLLs com LoadLibrary simples, que consulta o PATH.
    os.environ["PATH"] = os.pathsep.join([*map(str, dirs), os.environ.get("PATH", "")])
    return dirs
