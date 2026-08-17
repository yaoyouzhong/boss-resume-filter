"""Tk form dialog for editing an external candidate's profile fields.

Manual correction for imperfect resume parsing: the form is prefilled with
the record's pinned profile values and hands back the full set on save.
Filtering-relevant fields (gender/age/education/exp_years/salary/city/
job_status) trigger a rule re-run in the service layer; name/school/company
are display-only. All validation, re-scoring, persistence and re-evaluation
stay in the host — this module only builds widgets and returns user input.
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from tkinter import ttk
from typing import Protocol

import ui_theme
from ui_windowing import create_toplevel, place_window_centered


GENDER_OPTIONS: tuple[str, ...] = ("", "男", "女")
EDUCATION_OPTIONS: tuple[str, ...] = ("", "博士", "硕士", "本科", "大专", "高中", "中专")
JOB_STATUS_OPTIONS: tuple[str, ...] = ("", "离职", "在职", "应届", "在校", "暂不考虑")


class ExternalEditDialogHost(Protocol):
    """Visual host contract used by the external-edit dialog."""

    colors: Mapping[str, str]
    dpi_scale: float
    font_scale: float
    zoom_factor: float


@dataclass(frozen=True)
class ExternalEditFormData:
    """Full form values handed back to the host (unchanged fields included)."""

    name: str
    job_name: str
    gender: str
    age: str
    education: str
    exp_years: str
    salary: str
    city: str
    job_status: str
    school: str
    company: str


@dataclass(frozen=True)
class ExternalEditDialogWidgets:
    """Dialog references used by focused Tk tests."""

    window: tk.Toplevel
    name_var: tk.StringVar
    job_var: tk.StringVar
    gender_var: tk.StringVar
    age_var: tk.StringVar
    education_var: tk.StringVar
    exp_years_var: tk.StringVar
    salary_var: tk.StringVar
    city_var: tk.StringVar
    job_status_var: tk.StringVar
    school_var: tk.StringVar
    company_var: tk.StringVar
    feedback_var: tk.StringVar
    save_button: ttk.Button
    cancel_button: ttk.Button


def show_external_edit_dialog(
    host: ExternalEditDialogHost,
    parent: tk.Misc,
    *,
    font_family: str,
    candidate_name: str,
    initial: Mapping[str, str],
    current_job: str,
    job_names: Sequence[str],
    on_confirm: Callable[[ExternalEditFormData], bool],
) -> ExternalEditDialogWidgets:
    """Show the profile edit form; ``on_confirm`` receives the full form."""
    scale = host.dpi_scale * host.zoom_factor
    dialog_font_scale = host.font_scale * 0.88
    window = create_toplevel(parent)
    window.title("编辑候选人信息")
    window.transient(parent)
    window.grab_set()
    window.resizable(False, False)
    window.configure(background=host.colors["bg_main"])
    window.withdraw()

    style = ttk.Style(window)
    style.configure("ExtEdit.TLabel", background=host.colors["bg_main"])
    style.configure("ExtEdit.TFrame", background=host.colors["bg_main"])
    field_font = (font_family, int(13 * dialog_font_scale))
    hint_font = (font_family, int(11 * dialog_font_scale))
    muted_color = host.colors.get("text_muted", ui_theme.TEXT_MUTED)

    ttk.Label(
        window,
        text=f"编辑候选人信息 —— {candidate_name}",
        font=(font_family, int(16 * dialog_font_scale), "bold"),
        style="ExtEdit.TLabel",
    ).pack(pady=(int(16 * scale), int(2 * scale)))
    ttk.Label(
        window,
        text="修正后将按归属岗位规则重新评分；留空表示该字段未识别",
        font=hint_font,
        foreground=muted_color,
        style="ExtEdit.TLabel",
    ).pack(pady=(0, int(12 * scale)))

    form = ttk.Frame(window, style="ExtEdit.TFrame")
    form.pack(fill="x", padx=int(28 * scale))
    form.columnconfigure(1, weight=1)
    form.columnconfigure(3, weight=1)

    name_var = tk.StringVar(value=str(initial.get("name") or candidate_name))
    job_var = tk.StringVar(value=current_job)
    gender_var = tk.StringVar(value=str(initial.get("gender") or ""))
    age_var = tk.StringVar(value=str(initial.get("age") or ""))
    education_var = tk.StringVar(value=str(initial.get("education") or ""))
    exp_years_var = tk.StringVar(value=str(initial.get("exp_years") or ""))
    salary_var = tk.StringVar(value=str(initial.get("salary") or ""))
    city_var = tk.StringVar(value=str(initial.get("city") or ""))
    job_status_var = tk.StringVar(value=str(initial.get("job_status") or ""))
    school_var = tk.StringVar(value=str(initial.get("school") or ""))
    company_var = tk.StringVar(value=str(initial.get("company") or ""))
    feedback_var = tk.StringVar()

    row_pady = (int(5 * scale), int(5 * scale))

    def row_label(row: int, column: int, text: str) -> None:
        # 标签右对齐贴近输入框，与导入对话框及其他表单页面一致
        ttk.Label(
            form, text=text, font=field_font, style="ExtEdit.TLabel",
        ).grid(
            row=row, column=column, sticky="e", pady=row_pady,
            padx=(0, int(2 * scale)),
        )

    def entry_at(row: int, column: int, var: tk.StringVar, width: int = 18) -> None:
        ttk.Entry(form, textvariable=var, font=field_font, width=width).grid(
            row=row, column=column, sticky="ew", pady=row_pady,
            padx=(int(8 * scale), int(18 * scale)),
        )

    def combo_at(
        row: int,
        column: int,
        var: tk.StringVar,
        values: Sequence[str],
        width: int = 16,
    ) -> ttk.Combobox:
        combo = ttk.Combobox(
            form,
            textvariable=var,
            font=field_font,
            width=width,
            values=tuple(values),
            state="readonly",
        )
        combo.grid(
            row=row, column=column, sticky="ew", pady=row_pady,
            padx=(int(8 * scale), int(18 * scale)),
        )
        return combo

    # 两列布局：左列身份/学历，右列岗位/条件，展示字段独占整行
    row_label(0, 0, "姓名")
    entry_at(0, 1, name_var)
    row_label(0, 2, "归属岗位")
    job_combo = combo_at(0, 3, job_var, job_names)
    if len(job_names) <= 1:
        job_combo.state(["disabled"])

    row_label(1, 0, "性别")
    combo_at(1, 1, gender_var, GENDER_OPTIONS)
    row_label(1, 2, "年龄")
    entry_at(1, 3, age_var)

    row_label(2, 0, "学历")
    combo_at(2, 1, education_var, EDUCATION_OPTIONS)
    row_label(2, 2, "工作年限")
    entry_at(2, 3, exp_years_var)

    row_label(3, 0, "期望薪资")
    entry_at(3, 1, salary_var)
    row_label(3, 2, "期望城市")
    entry_at(3, 3, city_var)

    row_label(4, 0, "求职状态")
    combo_at(4, 1, job_status_var, JOB_STATUS_OPTIONS)

    row_label(5, 0, "毕业学校")
    ttk.Entry(form, textvariable=school_var, font=field_font).grid(
        row=5, column=1, columnspan=3, sticky="ew", pady=row_pady,
        padx=(int(8 * scale), 0),
    )
    row_label(6, 0, "最近公司")
    ttk.Entry(form, textvariable=company_var, font=field_font).grid(
        row=6, column=1, columnspan=3, sticky="ew", pady=row_pady,
        padx=(int(8 * scale), 0),
    )

    feedback_label = tk.Label(
        window,
        textvariable=feedback_var,
        font=hint_font,
        background=host.colors["bg_main"],
        foreground=host.colors.get("danger", "#d03050"),
        anchor="w",
        justify="left",
    )
    feedback_label.pack(fill="x", padx=int(28 * scale), pady=(int(4 * scale), 0))

    footer = ttk.Frame(window, style="ExtEdit.TFrame")
    footer.pack(fill="x", padx=int(28 * scale), pady=(int(10 * scale), int(16 * scale)))

    def close() -> None:
        try:
            window.grab_release()
        except tk.TclError:
            pass
        window.destroy()

    def confirm() -> None:
        name = name_var.get().strip()
        if not name:
            feedback_var.set("姓名不能为空。")
            return
        form_data = ExternalEditFormData(
            name=name,
            job_name=job_var.get().strip(),
            gender=gender_var.get().strip(),
            age=age_var.get().strip(),
            education=education_var.get().strip(),
            exp_years=exp_years_var.get().strip(),
            salary=salary_var.get().strip(),
            city=city_var.get().strip(),
            job_status=job_status_var.get().strip(),
            school=school_var.get().strip(),
            company=company_var.get().strip(),
        )
        if on_confirm(form_data):
            close()

    cancel_button = ttk.Button(footer, text="取消", command=close)
    cancel_button.pack(side="right")
    save_button = ttk.Button(
        footer, text="保存", command=confirm, style="Accent.TButton",
    )
    save_button.pack(side="right", padx=(0, int(8 * scale)))

    window.protocol("WM_DELETE_WINDOW", close)
    save_button.focus_set()
    # 内容构建完成后按实际高度居中，避免窗口底部留出大片空白。
    window.update_idletasks()
    place_window_centered(
        window,
        max(640, int(640 * scale)),
        window.winfo_reqheight(),
        parent=parent,
    )
    window.deiconify()
    return ExternalEditDialogWidgets(
        window=window,
        name_var=name_var,
        job_var=job_var,
        gender_var=gender_var,
        age_var=age_var,
        education_var=education_var,
        exp_years_var=exp_years_var,
        salary_var=salary_var,
        city_var=city_var,
        job_status_var=job_status_var,
        school_var=school_var,
        company_var=company_var,
        feedback_var=feedback_var,
        save_button=save_button,
        cancel_button=cancel_button,
    )
