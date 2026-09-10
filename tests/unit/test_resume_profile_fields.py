"""Regression cases for employer columns and wrapped school names."""
from bossmaster import _extract_company_from_resume, _extract_school_from_resume
from resume_profile_fields import company_from_work_table


HEADER = "工作经历\n起止时间 单位名称 工作经历简述\n"


def test_table_employer_uses_latest_period_in_either_reading_order():
    rows = (
        "2018.01-2020.12 甲方科技有限公司\n负责客户乙方公司的系统维护\n"
        "2021.01-至今 丙方科技有限公司\n负责开发\n"
    )
    columns = (
        "2018.01-2020.12\n2021.01-至今\n甲方科技有限公司\n"
        "负责客户乙方公司的系统维护\n丙方科技有限公司\n负责开发\n"
    )
    for body in (rows, columns):
        assert _extract_company_from_resume(HEADER + body) == "丙方科技有限公司"


def test_table_employer_handles_wrapped_date_and_company():
    body = "2021/04-\n至今\n甲方信息\n技术有限公司\n1.\n负责开发\n"
    assert company_from_work_table(HEADER + body) == "甲方信息技术有限公司"


def test_table_employer_accepts_abbreviation_and_descending_dates():
    body = "2022.11-至今 甲方科技\n2018.01-2022.10 乙方科技有限公司\n"
    assert company_from_work_table(HEADER + body) == "甲方科技"


def test_table_employer_does_not_invent_clipped_company_suffix():
    assert company_from_work_table(HEADER + "2022.11-至今 甲方科技股份有限公\n") == ""


def test_table_employer_rejects_ambiguous_columns():
    for body in (
        "2022.11-至今\n甲方科技\n乙方科技\n",
        "2022.11-至今\n负责客户甲方科技公司的项目开发\n",
        "2022.11-至今 甲方科技\n2022.11-至今 乙方科技\n",
        "2022.11-2021.10 甲方科技\n",
        "2022.11-2023.13 甲方科技\n",
    ):
        assert company_from_work_table(HEADER + body) == ""


def test_table_employer_ignores_project_section_companies():
    body = "2022.11-至今 甲方科技\n项目经历\n2023.01-至今 客户科技有限公司\n"
    assert company_from_work_table(HEADER + body) == "甲方科技"


def test_non_table_employer_keeps_existing_explicit_label():
    assert company_from_work_table("目前公司：甲方科技有限公司") is None
    assert _extract_company_from_resume("目前公司：甲方科技有限公司") == "甲方科技有限公司"
    assert _extract_company_from_resume(
        "目前公司：甲方科技有限公司\n" + HEADER + "2021.01-2022.01 乙方科技有限公司\n"
    ) == "甲方科技有限公司"


def test_wrapped_school_requires_complete_corroborating_name():
    label = "毕业学校：示例大学\n通达学院\n"
    assert _extract_school_from_resume(label + "教育经历\n示例大学通达学院 本科") == "示例大学通达学院"
    assert _extract_school_from_resume(label) == "示例大学"


def test_separate_faculty_does_not_extend_school_name():
    assert _extract_school_from_resume("毕业学校：示例大学\n计算机学院\n软件工程") == "示例大学"
