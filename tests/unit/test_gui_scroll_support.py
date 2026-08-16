import ctypes
from typing import Any

from gui_scroll_support import resolve_msg_send_double


def _three_arg_msg_send() -> Any:
    """Stand in for objc_msgSend as gui_scroll_support configures it.

    setup_cocoa_scroll_hook sets objc_msgSend.argtypes to three void pointers
    for the superview/isKindOfClass walk, which is exactly the state the double
    accessor has to cope with.
    """
    prototype = ctypes.CFUNCTYPE(
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    msg_send = prototype(lambda _self, _sel, _arg: 0)
    msg_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    return msg_send


class _AppleSiliconObjc:
    """libobjc as seen on arm64, where objc_msgSend_fpret does not exist."""

    def __init__(self) -> None:
        self.objc_msgSend = _three_arg_msg_send()

    def __getattr__(self, name: str):
        # ctypes raises AttributeError for symbols missing from the library;
        # objc_msgSend_fpret is x86_64-only, so arm64 must hit the fallback.
        raise AttributeError(f"dlsym: symbol not found: {name}")


class _IntelObjc:
    """libobjc as seen on x86_64, where objc_msgSend_fpret is available."""

    def __init__(self) -> None:
        self.objc_msgSend = _three_arg_msg_send()
        self.objc_msgSend_fpret = _three_arg_msg_send()


def test_arm64_fallback_targets_objc_msgsend_itself_not_a_python_callback():
    objc = _AppleSiliconObjc()

    resolved = resolve_msg_send_double(objc)

    # Passing the _FuncPtr object to CFUNCTYPE would wrap it in a fresh Python
    # callback trampoline at a different address; that trampoline re-enters
    # objc_msgSend under its three-argument argtypes and raises TypeError on
    # every scroll event, across a callback boundary where the caller's except
    # clause cannot catch it. Building from the address keeps a plain pointer.
    assert (
        ctypes.cast(resolved, ctypes.c_void_p).value
        == ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    )


def test_arm64_fallback_accepts_two_arguments():
    objc = _AppleSiliconObjc()

    resolved = resolve_msg_send_double(objc)

    assert tuple(resolved.argtypes) == (ctypes.c_void_p, ctypes.c_void_p)
    assert resolved.restype is ctypes.c_double


def test_intel_path_uses_fpret_with_double_return():
    objc = _IntelObjc()

    resolved = resolve_msg_send_double(objc)

    assert resolved is objc.objc_msgSend_fpret
    assert resolved.restype is ctypes.c_double
    assert tuple(resolved.argtypes) == (ctypes.c_void_p, ctypes.c_void_p)


def test_missing_msg_send_address_degrades_instead_of_raising():
    class _NullObjc:
        objc_msgSend = ctypes.c_void_p(None)

        def __getattr__(self, name: str):
            raise AttributeError(f"dlsym: symbol not found: {name}")

    assert resolve_msg_send_double(_NullObjc()) is None
