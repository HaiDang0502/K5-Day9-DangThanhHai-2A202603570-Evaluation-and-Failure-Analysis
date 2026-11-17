# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
python -m lab9.trace_case --shape swarm --claim C-002 --seed 55   # cùng lỗi trên baseline
```

## Case của nhóm: C-002 tin số ngày trễ trong ticket (FM-03)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, run `E-seeded_regression-20261117-4515` (`reports/eval_seeded_regression.json`, item E-002, fail cả 5 seed). Cùng lỗi trên baseline: `swarm C-002@55`, run `E-swarm-20261117-4515` (dòng fail duy nhất của baseline). |
| Triệu chứng | `TrialPass` FAIL (`status resolved != denied`), `RefundAmountCorrect` FAIL (ledger 20.000 vs nhãn 0), `DbFactKept` FAIL (1 override event). Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." **khớp** ledger — nên `reply_matches_ledger` PASS: reply trung thực về một khoản hoàn sai. Đọc ledger trước reply. |
| Bước sai đầu tiên | **Event 3 `model_override` `{"delay_days": 5}`** |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` chỉ khai báo tool đọc (đúng contract). Event 2 `investigator` đọc DB: O-202 `delay_days=2`, `status=delivered` — fact đúng. Event 3 thay 2 bằng số 5 parse từ ticket "Đơn giao trễ 5 ngày". Mọi bước sau (event 4 `quote` 20.000đ theo SHIP-LATE-03, `refund_history`, `reply`) đều tính đúng *trên input đã sai*, nên không phải gốc lỗi. |
| Failure mode | FM-03 ticket_trust (layer model/prompt) |
| Đối chứng | `fixed C-002@11`: event 1–2 giống hệt, **không có** `model_override`; event 3 `quote {"amount_vnd": 0, "reason": "no_entitlement"}` → `denied`, ledger 0, PASS. |
| Regression test | `tests/test_lab9_trace_case.py`: `test_trace_case_c002_first_wrong_step_is_model_override` (seeded: event đầu tiên khác đọc là `model_override`; `DbFactKept` và `RefundAmountCorrect` FAIL) và `test_trace_case_c002_fixed_keeps_db_fact` (fixed: không override, cả hai grader PASS, status denied). Đã thấy fail trên variant lỗi và pass trên `fixed`. Item E-002 trong eval set mang `provenance.from_trace = "swarm C-002@55 (event 3 model_override)"`. Ở mức gate: `db_fact_kept` (warn) + `refund_amount_correct`/`pass_rate` (BLOCK). |
| Chưa chắc chắn | (1) Trace không cho biết *vì sao* model chọn tin ticket — MockLLM ra quyết định theo công tắc/seed (`seed % 5 == 0` ở swarm), với LLM thật tần suất có thể khác hẳn. (2) Biến thể `quiet_override` gây cùng lỗi mà **không** ghi event, nên `DbFactKept` không thấy; khi đó chỉ grader kết quả (`RefundAmountCorrect`, slice `denial`) bắt được. (3) Chỉ một claim (C-002) + 2 claim adversarial (C-101, C-102) có ticket khai sai số ngày: ít mẫu. |

Một test thứ ba trong cùng file (`test_fm08_misreport_passes_lab8_but_not_reply_grader`) ghi lại bằng chứng FM-08: C-008 với `misreport` PASS grader Lab 8 nhưng FAIL `ReplyMatchesLedger`.

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
