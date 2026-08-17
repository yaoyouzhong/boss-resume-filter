"""Headless smoke: external candidate profile edit dialog."""
import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui_external_edit_dialog import show_external_edit_dialog


def _host():
    return SimpleNamespace(
        colors={
            "bg_main": "#f5f6f8",
            "danger": "#d03050",
            "text_muted": "#8a8f99",
        },
        dpi_scale=1.0,
        font_scale=1.0,
        zoom_factor=1.0,
    )


def _show(root, **kwargs):
    options = dict(
        font_family="Microsoft YaHei UI",
        candidate_name="鲍佳佳",
        initial={
            "gender": "女",
            "age": "31",
            "education": "本科",
            "exp_years": "2",
            "salary": "15-20K",
            "city": "南京",
            "job_status": "在职",
            "school": "南京林业大学",
            "company": "",
        },
        current_job="Java 岗",
        job_names=["Java 岗", "后端工程师"],
        on_confirm=lambda form: None,
    )
    options.update(kwargs)
    return show_external_edit_dialog(_host(), root, **options)


def check_prefill_and_collect(root):
    captured = {}
    def save(form):
        captured.update(form.__dict__)
        return True

    w = _show(root, on_confirm=save)
    # 预填当前记录值
    assert w.name_var.get() == "鲍佳佳"
    assert w.job_var.get() == "Java 岗"
    assert w.gender_var.get() == "女"
    assert w.education_var.get() == "本科"
    assert w.salary_var.get() == "15-20K"
    # 修改后保存：全量表单回传
    w.company_var.set("朗新科技")
    w.age_var.set("40")
    w.save_button.invoke()
    assert captured["name"] == "鲍佳佳"
    assert captured["company"] == "朗新科技"
    assert captured["age"] == "40"
    assert captured["education"] == "本科"  # 未修改字段原样回传
    assert captured["job_name"] == "Java 岗"
    assert not w.window.winfo_exists()
    print("prefill and collect OK")


def check_empty_name_blocked(root):
    w = _show(root)
    w.name_var.set("   ")
    w.save_button.invoke()
    assert "姓名不能为空" in w.feedback_var.get()
    assert w.window.winfo_exists(), "姓名非法时窗口应保持打开"
    w.window.destroy()
    print("empty name blocked OK")


def check_save_rejected_keeps_dialog_open(root):
    """宿主校验或持久化失败时，编辑值必须留在窗口供用户修正重试。"""
    w = _show(root, on_confirm=lambda _form: False)
    w.company_var.set("待修正公司")
    w.save_button.invoke()
    assert w.window.winfo_exists(), "宿主拒绝保存时编辑窗口不应关闭"
    assert w.company_var.get() == "待修正公司", "失败后不应丢失用户输入"
    w.window.destroy()
    print("save rejected keeps dialog open OK")


def check_single_job_disables_picker(root):
    w = _show(root, job_names=["Java 岗"])
    combos = [
        c for c in w.window.winfo_children()[2].winfo_children()
        if c.winfo_class() == "TCombobox"
    ]
    assert combos, "表单内应存在下拉框"
    job_combo = combos[0]  # 归属岗位是第一个下拉
    assert job_combo.instate(["disabled"]), "只有一个岗位时归属岗位应禁用"
    w.window.destroy()
    print("single job disables picker OK")


def main():
    root = tk.Tk()
    root.withdraw()
    check_prefill_and_collect(root)
    check_empty_name_blocked(root)
    check_save_rejected_keeps_dialog_open(root)
    check_single_job_disables_picker(root)
    root.destroy()
    print("EDIT_DIALOG_SMOKE_OK")


if __name__ == "__main__":
    main()
