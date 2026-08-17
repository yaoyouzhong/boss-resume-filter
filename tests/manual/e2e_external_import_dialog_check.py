"""Headless smoke: external import dialog single/batch phase transitions."""
import sys
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui_external_import_dialog import show_external_import_dialog


def _host():
    return SimpleNamespace(
        colors={
            "bg_main": "#f5f6f8",
            "danger": "#d03050",
            "text_muted": "#8a8f99",
            "primary": "#2563eb",
            "border": "#d5d9e0",
        },
        dpi_scale=1.0,
        font_scale=1.0,
        zoom_factor=1.0,
    )


def _show(root, **kwargs):
    options = dict(
        font_family="Microsoft YaHei UI",
        job_names=["岗位A", "岗位B"],
        default_job="岗位A",
        preview_file=lambda p: (True, "预览正常"),
        name_guesser=lambda p: Path(p).stem.split("-")[0],
        on_confirm=lambda form: None,
    )
    options.update(kwargs)
    return show_external_import_dialog(_host(), root, **options)


def check_single_mode(root):
    captured = {}

    def start_import(form):
        captured.update(form.__dict__)
        return True

    w = _show(root, on_confirm=start_import)
    win = w.window
    with patch(
        "gui_external_import_dialog.filedialog.askopenfilenames",
        return_value=("C:/tmp/张三-简历.txt",),
    ):
        # 先输入姓名再浏览：姓名不得被清空
        w.name_var.set("手动姓名")
        for child in win.winfo_children():
            pass
        # 找到浏览按钮并点击
        form = [c for c in win.winfo_children() if c.winfo_class() == "TFrame"][0]
        browse = [
            c for c in form.winfo_children()
            if c.winfo_class() == "TButton" and c.cget("text") == "浏览…"
        ][0]
        browse.invoke()
    assert w.file_var.get() == "C:/tmp/张三-简历.txt"
    assert w.name_var.get() == "手动姓名", f"姓名被清空: {w.name_var.get()!r}"
    assert str(w.confirm_button.cget("text")) == "导入"
    # 预解析已改为后台线程执行：等待其完成、按钮恢复可用
    for _ in range(200):
        root.update()
        if w.confirm_button.instate(["!disabled"]):
            break
        time.sleep(0.02)
    else:
        raise AssertionError("预解析未在预期时间内完成")
    w.channel_var.set("猎头")
    w.confirm_button.invoke()
    assert captured["file_paths"] == ("C:/tmp/张三-简历.txt",)
    assert captured["name"] == "手动姓名"
    root.update_idletasks()
    assert win.winfo_exists(), "后台导入期间对话框应保留并显示中间状态"
    assert w.progress_var.get() == "正在导入 手动姓名 的简历，解析与评分中…"
    assert str(w.progress_bar.cget("mode")) == "indeterminate"
    assert w.confirm_button.winfo_manager() == ""
    assert w.cancel_button.instate(["disabled"])

    # 宿主后台任务完成后才关闭进行中视图。
    w.close_dialog()
    assert not win.winfo_exists()

    # 前置确认被取消（例如同名查重弹窗选择取消）时，不得进入进行中状态。
    blocked = _show(root, on_confirm=lambda _form: False, preview_file=None)
    blocked.file_var.set("C:/tmp/李四-简历.txt")
    blocked.name_var.set("李四")
    blocked.channel_var.set("猎头")
    # selected_paths 只能通过浏览动作写入，模拟一次真实单选。
    with patch(
        "gui_external_import_dialog.filedialog.askopenfilenames",
        return_value=("C:/tmp/李四-简历.txt",),
    ):
        blocked_form = [
            c for c in blocked.window.winfo_children() if c.winfo_class() == "TFrame"
        ][0]
        blocked_browse = [
            c for c in blocked_form.winfo_children()
            if c.winfo_class() == "TButton" and c.cget("text") == "浏览…"
        ][0]
        blocked_browse.invoke()
    blocked.confirm_button.invoke()
    assert blocked.window.winfo_exists()
    assert blocked.confirm_button.winfo_manager() == "pack"
    assert not blocked.cancel_button.instate(["disabled"])
    blocked.close_dialog()
    print("single mode OK")


def check_batch_mode(root):
    runs = {}
    summary_box = {}

    def fake_run_batch(paths, form, callbacks):
        runs["paths"] = list(paths)
        runs["form"] = form
        runs["callbacks"] = callbacks
        return lambda: runs.setdefault("stopped", True)

    w = _show(root, run_batch=fake_run_batch)
    win = w.window
    form = [c for c in win.winfo_children() if c.winfo_class() == "TFrame"][0]
    browse = [
        c for c in form.winfo_children()
        if c.winfo_class() == "TButton" and c.cget("text") == "浏览…"
    ][0]
    with patch(
        "gui_external_import_dialog.filedialog.askopenfilenames",
        return_value=("C:/tmp/张三-简历.txt", "C:/tmp/李四-简历.txt", "C:/tmp/王五-简历.txt"),
    ):
        browse.invoke()
    assert w.file_var.get() == "已选 3 个文件", w.file_var.get()
    assert str(w.name_entry_state if hasattr(w, "name_entry_state") else "") == ""
    assert str(w.confirm_button.cget("text")) == "批量导入"
    assert "自动提取" in w.name_var.get()

    w.channel_var.set("内推")
    w.confirm_button.invoke()
    assert runs["paths"] == ["C:/tmp/张三-简历.txt", "C:/tmp/李四-简历.txt", "C:/tmp/王五-简历.txt"]
    assert runs["form"].job_name == "岗位A"
    assert runs["form"].source_channel == "内推"
    assert str(w.cancel_button.cget("text")) == "取消导入"
    assert w.progress_var.get().startswith("正在导入 0/3")

    cb = runs["callbacks"]
    item = SimpleNamespace(name="张三", status="imported", score=72, reason="", path="C:/tmp/张三-简历.txt", name_needs_review=False)
    item2 = SimpleNamespace(name="李四", status="rejected", score=30, reason="学历不满足", path="C:/tmp/李四-简历.txt", name_needs_review=False)
    item3 = SimpleNamespace(name="resume_X", status="failed", score=0, reason="无法解析", path="C:/tmp/王五-简历.txt", name_needs_review=True)
    # 低分导入（39 分 < 55 通过线）：入库但进淘汰记录，汇总须单列说明
    item4 = SimpleNamespace(name="朱建杨", status="imported", score=39, reason="评分低于 55 分，记录已进入淘汰记录", path="C:/tmp/朱建杨-简历.txt", name_needs_review=False)
    cb.on_progress(1, 4, item)
    assert "1/4" in w.progress_var.get()
    summary = SimpleNamespace(items=(item, item2, item3, item4), stopped=False)
    cb.on_import_done(summary)
    # 汇总分两层：summary_var 是主结论行，次要口径在 summary_detail_var 灰显
    main_text = w.summary_var.get()
    detail_text = w.summary_detail_var.get()
    text_now = f"{main_text}\n{detail_text}"
    summary_box["text"] = text_now
    assert "导入完成" in main_text and "入库 3 人" in main_text, f"主结论行缺失: {main_text!r}"
    assert "通过筛选 2 人" in main_text and "未通过 1 人" in main_text
    assert "失败 1 人" in detail_text, f"次要口径未灰显: {detail_text!r}"
    assert "重复跳过" not in text_now, f"零值项不应占文案: {text_now!r}"
    assert "未通过筛选的记录已入淘汰记录" in text_now, f"淘汰去向未说明: {text_now!r}"
    assert "通过者中 1 人低于 55 分" in text_now, f"低分导入未在汇总中单列: {text_now!r}"
    rows = w.summary_tree.get_children()
    assert len(rows) == 4
    assert int(w.summary_tree.cget("height")) == 4, "5 条以内应整表全展示"
    values = [w.summary_tree.item(r)["values"] for r in rows]
    assert values[0][2] == "已导入（72 分）"
    assert values[1][2] == "未通过筛选（参考 30 分）", f"淘汰行未展示参考分: {values[1][2]!r}"
    assert "待核对" in values[2][1]
    assert values[3][2] == "低于通过线（39 分）" and "淘汰记录" in values[3][3]
    # 汇总视图必须排在按钮行（footer）之前——pack 顺序错误会把按钮悬在半空
    pack_slaves = win.pack_slaves()
    summary_frame = w.summary_label.master
    footer = w.cancel_button.master
    assert pack_slaves.index(summary_frame) < pack_slaves.index(footer), (
        "汇总视图落到了按钮行之后"
    )
    # 汇总行可能超出窗口宽度：必须设置折行宽度，避免右侧文字被裁掉
    assert int(w.summary_label.cget("wraplength")) > 0
    assert str(w.cancel_button.cget("text")) == "关闭"
    cb.on_eval_progress(1, 1, "张三")
    assert "简历评估中 1/1" in w.eval_var.get()
    cb.on_all_done(summary, "简历评估完成 1 人。")
    assert w.eval_var.get() == "简历评估完成 1 人。"
    win.destroy()
    print("batch mode OK")


def check_name_autofill_refresh(root):
    """自动提取的姓名随换文件更新；用户手动改过的姓名不被覆盖。"""
    w = _show(root)
    win = w.window
    form = [c for c in win.winfo_children() if c.winfo_class() == "TFrame"][0]
    browse = [
        c for c in form.winfo_children()
        if c.winfo_class() == "TButton" and c.cget("text") == "浏览…"
    ][0]
    with patch(
        "gui_external_import_dialog.filedialog.askopenfilenames",
        return_value=("C:/tmp/孙伟斌-简历.txt",),
    ):
        browse.invoke()
    assert w.name_var.get() == "孙伟斌"
    with patch(
        "gui_external_import_dialog.filedialog.askopenfilenames",
        return_value=("C:/tmp/丁小飞.docx",),
    ):
        browse.invoke()
    assert w.name_var.get() == "丁小飞", f"换文件后姓名未更新: {w.name_var.get()!r}"
    w.name_var.set("手动改的名字")
    with patch(
        "gui_external_import_dialog.filedialog.askopenfilenames",
        return_value=("C:/tmp/张三-简历.txt",),
    ):
        browse.invoke()
    assert w.name_var.get() == "手动改的名字", "手动修改的姓名被覆盖"
    win.destroy()
    print("name autofill OK")


def _find_descendants(widget, class_name):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == class_name:
            found.append(child)
        found.extend(_find_descendants(child, class_name))
    return found


def check_ai_enhance_switch(root):
    """AI 增强开关：不可用时强制关闭且禁用，可用时切换触发回调并进表单。"""
    # 不可用：即使上次开启也被强制关闭，回退 Checkbutton 处于禁用态
    toggled = []
    w = _show(
        root,
        ai_enhance_available=False,
        ai_enhance_initial=True,
        on_ai_enhance_toggle=toggled.append,
    )
    assert hasattr(w, "ai_enhance_var"), "widgets 缺少 ai_enhance_var"
    assert w.ai_enhance_var.get() is False, "不可用时不应保留开启状态"
    form = [c for c in w.window.winfo_children() if c.winfo_class() == "TFrame"][0]
    # 开关与说明文字同行放在一个容器里，Checkbutton 不再是 form 直接子级
    check = _find_descendants(form, "TCheckbutton")[0]
    assert check.instate(["disabled"]), "不可用时开关应禁用"
    w.window.destroy()

    # 可用：切换触发持久化回调，confirm 收集 ai_enhance=True
    toggled.clear()
    captured = {}
    w = _show(
        root,
        ai_enhance_available=True,
        ai_enhance_initial=False,
        on_ai_enhance_toggle=toggled.append,
        ai_resume_eval_available=True,
        ai_resume_eval_initial=False,
        ai_model_label="OpenAI / gpt-test",
        on_confirm=lambda form_data: captured.update(form_data.__dict__),
    )
    assert w.ai_enhance_var.get() is False
    w.ai_enhance_var.set(True)  # trace 与真实开关点击走同一通路
    assert toggled == [True], f"切换未触发持久化回调: {toggled!r}"
    w.ai_resume_eval_var.set(True)
    form = [c for c in w.window.winfo_children() if c.winfo_class() == "TFrame"][0]
    browse = [
        c for c in form.winfo_children()
        if c.winfo_class() == "TButton" and c.cget("text") == "浏览…"
    ][0]
    with patch(
        "gui_external_import_dialog.filedialog.askopenfilenames",
        return_value=("C:/tmp/张三-简历.txt",),
    ):
        browse.invoke()
    for _ in range(200):
        root.update()
        if w.confirm_button.instate(["!disabled"]):
            break
        time.sleep(0.02)
    else:
        raise AssertionError("预解析未在预期时间内完成")
    w.name_var.set("张三")
    w.channel_var.set("猎头")
    w.confirm_button.invoke()
    assert captured["ai_enhance"] is True, f"表单未收集开关: {captured!r}"
    assert captured["ai_resume_eval"] is True, f"表单未收集简历评估开关: {captured!r}"
    print("ai enhance switch OK")


def check_batch_scrollbar_over_five(root):
    """超过 5 条记录：树固定 5 行高并显示纵向滚动条。"""
    runs = {}

    def fake_run_batch(paths, form, callbacks):
        runs["callbacks"] = callbacks
        return lambda: None

    w = _show(root, run_batch=fake_run_batch)
    form = [c for c in w.window.winfo_children() if c.winfo_class() == "TFrame"][0]
    browse = [
        c for c in form.winfo_children()
        if c.winfo_class() == "TButton" and c.cget("text") == "浏览…"
    ][0]
    with patch(
        "gui_external_import_dialog.filedialog.askopenfilenames",
        return_value=tuple(f"C:/tmp/r{i}.txt" for i in range(7)),
    ):
        browse.invoke()
    w.channel_var.set("内推")
    w.confirm_button.invoke()
    items = tuple(
        SimpleNamespace(
            name=f"候选人{i}", status="imported", score=70, reason="",
            path=f"C:/tmp/r{i}.txt", name_needs_review=False,
        )
        for i in range(7)
    )
    runs["callbacks"].on_import_done(SimpleNamespace(items=items, stopped=False))
    assert int(w.summary_tree.cget("height")) == 5, "超过 5 条应固定 5 行高"
    holder = w.summary_tree.master
    scrolls = [c for c in holder.winfo_children() if c.winfo_class() == "TScrollbar"]
    assert scrolls and scrolls[0].winfo_manager() == "pack", "超过 5 条应显示滚动条"
    # 结果/原因列加宽后总列宽超过可视区：横向滚动条常驻
    assert len(scrolls) == 2, f"应同时存在纵横两个滚动条: {len(scrolls)}"
    assert str(scrolls[1].cget("orient")) == "horizontal"
    assert scrolls[1].winfo_manager() == "pack", "横向滚动条应常驻"
    w.window.destroy()
    print("batch scrollbar OK")


def main():
    root = tk.Tk()
    root.withdraw()
    check_single_mode(root)
    check_batch_mode(root)
    check_name_autofill_refresh(root)
    check_ai_enhance_switch(root)
    check_batch_scrollbar_over_five(root)
    root.destroy()
    print("DIALOG_BATCH_SMOKE_OK")


if __name__ == "__main__":
    main()
