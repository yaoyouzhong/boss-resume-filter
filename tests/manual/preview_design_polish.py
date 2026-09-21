"""Preview design changes using isolated synthetic data and no network/keyring access."""
from __future__ import annotations
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import tkinter as tk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
for key in ("DATA_MIGRATION", "GUARD_PERSISTENCE", "STARTUP_UPDATE"):
    os.environ[f"BOSS_RESUME_FILTER_DISABLE_{key}"] = "1"


def main():
    demo = runpy.run_path(str(ROOT / "docs/assets/user-guide/generate_user_guide_screenshots.py"))
    g = demo["gui_main"]
    with tempfile.TemporaryDirectory(prefix="boss-design-preview-") as folder:
        sandbox = Path(folder)
        for name, filename in (("CONFIG_PATH", "jobs.json"), ("CONFIG_BACKUP_PATH", "jobs.bak"),
                               ("CANDIDATES_PATH", "candidates.json"), ("CANDIDATES_XLSX_PATH", "candidates.xlsx"),
                               ("CONTACT_QUEUE_PATH", "queue.json"), ("RUN_PREFERENCES_PATH", "run.json")):
            setattr(g, name, sandbox / filename)
        g.CONFIG_PATH.write_text(json.dumps({"jobs": {demo["DEMO_JOB"]: demo["DEMO_JOB_RULE"]}}, ensure_ascii=False), encoding="utf-8")
        g.CANDIDATES_PATH.write_text(json.dumps(demo["build_demo_candidates"](), ensure_ascii=False), encoding="utf-8")
        config_path = sandbox / "api.json"
        config_path.write_text(json.dumps(demo["DEMO_API_CONFIG"]), encoding="utf-8")
        g.get_api_config_path = lambda **kwargs: config_path
        g.get_api_key = lambda *args, **kwargs: ""
        g.BossFilterGUI._read_runtime_api_key = lambda *args, **kwargs: ""
        for name in ("_load_startup_updater", "refresh_home_status", "_start_browser_auto_check",
                     "_stop_browser_auto_check", "_schedule_api_key_resolution", "_schedule_run_page_api_key_check"):
            setattr(g.BossFilterGUI, name, lambda *args, **kwargs: None)
        g._enable_high_dpi_awareness()
        root = tk.Tk()
        app = g.BossFilterGUI(root, education_api_config_path=config_path, run_preferences_path=g.RUN_PREFERENCES_PATH)
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.title("BOSS 设计验收 · 演示数据")
        root.geometry("1500x980+100+80")
        errors = []
        def report_error(exc, value, tb):
            import traceback
            errors.append(str(value))
            traceback.print_exception(exc, value, tb)
        root.report_callback_exception = report_error
        pages = ("home", "config", "run", "result", "education", "stats", "api")
        creators = {"home": "create_home_page", "config": "create_config_page", "run": "create_run_page",
                    "result": "create_result_page", "education": "create_education_page",
                    "stats": "create_stats_page", "api": "create_api_config_page"}
        attributes = {page: f"{page}_page" for page in pages}
        attributes["api"] = "api_config_page"
        for page in pages:
            if getattr(app, attributes[page]) is None:
                getattr(app, creators[page])()
            getattr(app, f"show_page_{page}")()
            root.update()
            assert getattr(app, attributes[page]).winfo_ismapped()
            print(f"PASS page {page}", flush=True)
        # Queue state transitions must move the emphasis without running recognition or verification.
        from PIL import Image, ImageDraw
        for index, status in enumerate(("已识别", "待人工确认", "待识别")):
            fixture = Image.new("RGB", (800, 550), "white")
            draw = ImageDraw.Draw(fixture)
            draw.rectangle((35, 35, 765, 515), outline="#2563EB", width=3)
            draw.text((80, 100), "DEMO CERTIFICATE - UI TEST ONLY", fill="black")
            fixture.save(sandbox / f"演示证书{index}.png")
            item_id = f"demo-{index}"
            app.education_items[item_id] = {
                "path": str(sandbox / f"演示证书{index}.png"), "name": f"演示人员{index}",
                "certificate_number": f"DEMO0000000000{index}", "certificate_type": "education",
                "status": status, "school": "", "major": "", "warnings": "",
            }
            app.education_queue_tree.insert("", "end", iid=item_id)
            app._update_education_queue_row(item_id)
        app._refresh_education_queue_summary()
        app._refresh_education_action_states()
        assert str(app.education_recognize_btn.cget("style")) == "Step.Primary.TButton"
        app.education_items["demo-2"]["status"] = "已识别"
        app.education_items["demo-1"].update(status="信息已修改", manually_edited=True)
        app._refresh_education_action_states()
        assert str(app.education_fill_btn.cget("style")) == "Step.Primary.TButton"
        for item_id in app.education_items:
            app._update_education_queue_row(item_id)
        app._refresh_education_queue_summary()
        print("PASS education step emphasis follows state", flush=True)
        app.show_page_education()
        root.update()
        preview = app.education_preview_label
        preview.configure(image="", text="无法加载演示证书：" + "long-path/" * 80)
        root.update()
        panels = app.education_workspace.winfo_children()
        assert len(panels) == 2 and all(panel.winfo_width() > 200 for panel in panels)
        assert int(preview.cget("wraplength")) > 0
        preview.configure(text="请选择证书查看预览")
        print("PASS long preview message preserves both panels", flush=True)

        app.show_page_result()
        root.update()
        # Compact/normal transitions retain useful table height and keyboard metric activation.
        for height in (760, 980):
            root.geometry(f"1500x{height}+100+80")
            root.update()
            app.layout_support.update_result_stats_compact()
            root.update()
            assert app.result_tree.winfo_height() > 200
            cards = [icon.master.master for icon, _text_column in app._result_stat_icon_canvases]
            assert max(card.winfo_width() for card in cards) - min(card.winfo_width() for card in cards) <= 1
            for card in cards:
                body = card.winfo_children()[0]
                assert abs(body.winfo_x() + body.winfo_width() / 2 - card.winfo_width() / 2) <= 1
        print("PASS metric cards equal width and centered at both window heights", flush=True)
        called = []
        show_detail = app.show_result_stat_detail
        app.show_result_stat_detail = called.append
        metric = app._result_stat_icon_canvases[0][0].master.master
        metric.focus_force()
        root.update()
        metric.event_generate("<Return>")
        root.update()
        assert called == ["strong"]
        app.show_result_stat_detail = show_detail
        print("PASS keyboard metric activation", flush=True)
        app.show_page_run()
        root.update()
        for height in (760, 980):
            root.geometry(f"1500x{height}+100+80")
            root.update()
            for position in (0, 1):
                app.run_canvas.yview_moveto(position)
                root.update()
                assert app.start_btn.winfo_ismapped() and app.stop_btn.winfo_ismapped()
                assert app.start_btn.winfo_rooty() >= app.run_page.winfo_rooty()
                assert app.start_btn.winfo_rooty() < app.run_canvas.winfo_rooty()
                assert app.stop_btn.winfo_rootx() + app.stop_btn.winfo_width() <= app.run_page.winfo_rootx() + app.run_page.winfo_width()
            assert app.log_text.winfo_rooty() + 150 < app.run_canvas.winfo_rooty() + app.run_canvas.winfo_height()
        app.is_running = False
        app.progress_label.configure(text="")
        app._refresh_run_progress_layout()
        root.update()
        idle_height = app.run_canvas.winfo_height()
        assert not app.run_progress_frame.winfo_ismapped()
        app.is_running = True
        app.progress_label.configure(text="35%  演示进度")
        app._refresh_run_progress_layout()
        root.update()
        assert app.progress_bar.winfo_ismapped()
        assert app.run_canvas.winfo_height() < idle_height
        app.is_running = False
        app.progress_label.configure(text="已完成 · 演示结果")
        app._refresh_run_progress_layout()
        root.update()
        assert not app.progress_bar.winfo_ismapped()
        assert app.progress_label.winfo_ismapped()
        app.progress_label.configure(text="")
        app._refresh_run_progress_layout()
        print("PASS idle, running and terminal progress layouts", flush=True)
        app.scan_advanced_toggle_label.event_generate("<Button-1>")
        root.update()
        assert app.scan_advanced_details_frame.winfo_ismapped()
        app.scan_advanced_toggle_label.event_generate("<Button-1>")
        root.update()
        print("PASS run controls visible on entry and while viewing logs; advanced settings toggle", flush=True)
        app.show_page_result()
        root.update()
        app.feedback_support.show_inline_banner(app.result_page, "success", "短提示", duration_ms=10)
        app.feedback_support.show_inline_banner(app.result_page, "error", "演示错误：请检查后重试")
        root.after(40, lambda: root.quit())
        root.mainloop()
        assert app.result_page in app._inline_banners
        app.feedback_support.hide_inline_banner(app.result_page)
        assert not errors, errors
        print("PASS persistent error replaces timed success", flush=True)
        if "--verify" in sys.argv:
            root.destroy()
        else:
            app.show_page_run()
            root.update()
            app.run_canvas.yview_moveto(0)
            root.mainloop()


if __name__ == "__main__":
    main()
