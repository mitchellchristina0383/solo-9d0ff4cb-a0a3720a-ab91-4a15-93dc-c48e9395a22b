"""命令行演示：跑通“半张卡纸”和“停稳前出纸”两条验收路径。

用法：
    python3 scripts/demo.py [数据库路径]

脚本只通过 HTTP 接口操作，结束后打印关键出纸记录。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json  # noqa: E402

from printroom.server import create_server  # noqa: E402


def call(base, method, path, body=None):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def show(title):
    print(f"\n=== {title} ===")


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        tempfile.mkdtemp(prefix="printroom-demo-"), "demo.db"
    )
    server = create_server(db_path, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body=None):
        code, data = call(base, "POST", path, body if body is not None else {})
        if code >= 400:
            print(f"  ! {path} -> {code} {data.get('error')}")
        return code, data

    def make_job(title, pages):
        _, doc = post("/api/documents", {"title": title})
        for i in range(pages):
            post(f"/api/documents/{doc['id']}/pages", {"content": f"第 {i + 1} 页"})
        _, job = post(f"/api/documents/{doc['id']}/finalize")
        print(f"  作业 #{job['id']} 《{title}》 {pages} 页 / {job['total_sheets']} 张")
        return job["id"]

    def sheet(job_id, no, sides=("front", "back")):
        _, job = post(f"/api/jobs/{job_id}/print", {"side": "front", "sheet_no": no})
        for side in sides[1:]:
            _, job = post(f"/api/jobs/{job_id}/print", {"side": side, "sheet_no": no})
        return job

    try:
        show("试跑一：半张卡纸 → 停稳 → 换机续印（6 页 / 3 张）")
        job_id = make_job("半张卡纸演示", 6)
        post(f"/api/jobs/{job_id}/start", {"printer_id": "P1"})
        print("  一号机完整出第 1 张")
        sheet(job_id, 1)
        print("  第 2 张只出了正面，报告卡纸")
        post(f"/api/jobs/{job_id}/print", {"side": "front", "sheet_no": 2})
        post(f"/api/jobs/{job_id}/jam")
        code, _ = post(f"/api/jobs/{job_id}/switch", {})
        print(f"  停稳前尝试换机 -> {code}（应被拒绝）")
        post(f"/api/jobs/{job_id}/stop")
        print("  原机已确认停稳，派续单到二号机")
        post(f"/api/jobs/{job_id}/switch", {})
        _, job = post(f"/api/jobs/{job_id}/switch", {})
        conts = [s for s in job["segments"] if s["kind"] == "continuation"]
        print(f"  续单数量 = {len(conts)}（重复换机不重复派单），从第 {conts[0]['start_sheet']} 张起")
        sheet(job_id, 2)
        sheet(job_id, 3)
        _, job = call(base, "GET", f"/api/jobs/{job_id}")
        print(f"  作业状态 {job['status']}，完整出纸 {job['printed_sheets']}/{job['total_sheets']} 张")

        show("试跑二：停稳前完整出纸都算数（8 页 / 4 张）")
        job2 = make_job("停稳前出纸演示", 8)
        post(f"/api/jobs/{job2}/start", {"printer_id": "P1"})
        sheet(job2, 1)
        sheet(job2, 2)
        print("  第 1、2 张已完整出纸")
        post(f"/api/jobs/{job2}/print", {"side": "front", "sheet_no": 3})
        post(f"/api/jobs/{job2}/jam")
        post(f"/api/jobs/{job2}/stop")
        post(f"/api/jobs/{job2}/switch", {})
        _, job = call(base, "GET", f"/api/jobs/{job2}")
        print(f"  停稳后仍保留完整出纸 {job['printed_sheets']} 张，从第 {job['current_sheet']} 张续印")
        sheet(job2, 3)
        sheet(job2, 4)
        _, job = call(base, "GET", f"/api/jobs/{job2}")
        print(f"  作业状态 {job['status']}，完整出纸 {job['printed_sheets']}/{job['total_sheets']} 张")

        show("试跑三：两个网页交错——换稿与开打（讲义排队后改版稿才到）")
        # 讲义 v1 已排队
        _, doc_v1 = post("/api/documents", {"title": "讲义v1"})
        for i in range(2):
            post(f"/api/documents/{doc_v1['id']}/pages", {"content": f"v1 第 {i + 1} 页"})
        _, job3 = post(f"/api/documents/{doc_v1['id']}/finalize")
        job3_id = job3["id"]
        print(f"  作业 #{job3_id} 《讲义v1》 2 页已排队（第 {job3['document_version']} 版）")

        # 页面甲打开，记下当前稿；页面乙录入改版稿（不提交入队）
        _, state = call(base, "GET", "/api/state")
        seen_by_a = next(j for j in state["jobs"] if j["id"] == job3_id)["document_id"]
        print(f"  页面甲打开，看到当前稿为文档 #{seen_by_a}")
        _, doc_v2 = post("/api/documents", {"title": "讲义v2改版"})
        for i in range(4):
            post(f"/api/documents/{doc_v2['id']}/pages", {"content": f"v2 第 {i + 1} 页"})
        print(f"  页面乙录入改版稿（文档 #{doc_v2['id']}，4 页，未入队），给作业 #{job3_id} 换稿")
        code, job3 = post(f"/api/jobs/{job3_id}/swap-document", {"document_id": doc_v2["id"]})
        print(f"  换稿 -> {code}：作业号 #{job3['id']} 不变，第 {job3['document_version']} 版，"
              f"{job3['page_count']} 页 / {job3['total_sheets']} 张按新版重算")

        # 页面甲仍持旧稿点开打：须提示重新确认，机台不启动
        code, body = post(f"/api/jobs/{job3_id}/start",
                          {"printer_id": "P1", "expected_document_id": seen_by_a})
        print(f"  页面甲（旧页面）点开打 -> {code} {body.get('code')}：{body.get('error')}")
        _, state = call(base, "GET", "/api/state")
        p1 = next(p for p in state["printers"] if p["id"] == "P1")
        j3 = next(j for j in state["jobs"] if j["id"] == job3_id)
        print(f"  机台未启动：一号机 {p1['state']}，作业仍 {j3['status']}")

        # 页面甲刷新后按新稿重新确认开打
        code, job3 = post(f"/api/jobs/{job3_id}/start",
                          {"printer_id": "P1", "expected_document_id": doc_v2["id"]})
        print(f"  页面甲刷新后重新确认开打 -> {code}，按第 {job3['document_version']} 版出纸")
        sheet(job3_id, 1)
        sheet(job3_id, 2)
        _, job3 = call(base, "GET", f"/api/jobs/{job3_id}")
        print(f"  作业状态 {job3['status']}，末张页码 正{job3['records'][0]['front_page']}"
              f"/背{job3['records'][0]['back_page']}（按改版 4 页算）")

        # 已开打的作业拒绝换稿（用另一个在印作业演示）
        _, doc_v3 = post("/api/documents", {"title": "讲义v3再改版"})
        post(f"/api/documents/{doc_v3['id']}/pages", {"content": "v3 第 1 页"})
        job4 = make_job("照常开打的旧作业", 2)
        post(f"/api/jobs/{job4}/start", {"printer_id": "P1"})
        code, body = post(f"/api/jobs/{job4}/swap-document", {"document_id": doc_v3["id"]})
        print(f"  作业 #{job4} 已开打，换稿 -> {code} {body.get('code')}（应被拒绝）")
        sheet(job4, 1)
        _, job4 = call(base, "GET", f"/api/jobs/{job4}")
        print(f"  旧作业（从未换稿）照常开打并完成：{job4['status']}")

        show("作业 #{0} 的换稿记录（重启后仍可查）".format(job3_id))
        _, job3 = call(base, "GET", f"/api/jobs/{job3_id}")
        for v in job3["document_history"]:
            mark = "当前用稿" if v["active"] else "已换下"
            print(f"  第 {v['version']} 版 · 文档 #{v['document_id']}《{v['title']}》 "
                  f"{v['page_count']} 页 · {mark} · {v['since']} 起")

        show("出纸记录（网页“出纸记录”表同源）")
        _, state = call(base, "GET", "/api/state")
        for rec in reversed(state["records"]):
            back = "-" if rec["back_page"] is None else rec["back_page"]
            print(
                f"  作业#{rec['job_id']} {rec['printer_name']} 第{rec['sheet_no']}张 "
                f"正{rec['front_page']}/背{back} {rec['label']} {rec['scrap_reason'] or ''}"
            )
        print(f"\n数据库：{db_path}")
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
