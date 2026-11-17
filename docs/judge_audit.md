# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (positive class = FAIL: việc của judge là *tìm* lỗi).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **PASS** — tp 24, fp 4, fn 6, tn 66 | **dùng** (non-blocking, xem dưới) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **FAIL** — tp 9, fp 0, fn 21, tn 70 | **loại** |

Tỉ lệ lỗi: tone quan sát 0.28 → Rogan-Gladen hiệu chỉnh **0.30** (đúng bằng tỉ lệ người: 30/100). Helpful quan sát 0.09 → hiệu chỉnh **0.30** — judge chỉ thấy chưa tới 1/3 số lỗi thật.

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú → sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | **Người** | Xưng "em" + "nha" quá suồng sã với khách. Rubric tone: thêm "xưng hô chuẩn: LogiX/anh/chị; cấm em/bạn/nha/nhé" kèm ví dụ FAIL. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ…rồi nha." | PASS | FAIL | **Người** | Judge coi "bạn ơi" là thân thiện. Cùng sửa như #1; thêm few-shot có "Dạ" ở đầu nhưng vẫn FAIL vì xưng hô (chữ "Dạ" không cứu cả câu). |
| 3 | H-029 "Dạ, vui lòng chờ…không cần nhắn thêm." | PASS | FAIL | **Người** | Giọng ra lệnh; judge chỉ khớp từ lịch sự bề mặt. Rubric: "không ra lệnh hay chặn khách liên hệ (vd 'không cần nhắn thêm')". |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | **Người** | Judge phạt vì "thiếu xin lỗi". Rubric: ghi rõ "xin lỗi KHÔNG bắt buộc khi yêu cầu được giải quyết; ngắn gọn đủ ý là PASS". |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ cho đơn O-202 ạ." | FAIL | PASS | **Người** | Style guide CS LogiX cho phép "refund". Rubric: whitelist thuật ngữ được phép (refund, ship, COD). |
| 6 | HL-015 "Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | **Người** | Judge không thấy ledger nên không thể biết sai tiền. **Không sửa rubric judge** — đây là việc của code: `reply_matches_ledger` (FM-08). |
| 7 | HL-016 / HL-043 xin lỗi dài, số tiền sai (ledger 35.000 / 20.000) | PASS | FAIL | **Người** | Văn xin lỗi dài khiến judge chấm "helpful". Lỗi thật là sai tiền → cùng hướng #6. |
| 8 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | **Người** | Sai xưng hô + không nói kết quả; thuộc tiêu chí tone (đã có judge tone đạt floors) và "nêu kết quả cụ thể". |

## Kết luận
- **helpful bị loại** vì recall 0.30 (21/30 lỗi thật bị bỏ sót, fn=21) và κ=0.375 < 0.6. Precision 1.0 không cứu được: judge nói FAIL thì đúng, nhưng hầu như không nói FAIL. Dùng nó làm gate = gate "xanh" trên 70% reply hỏng. Phần lớn fn là **sai số tiền** (HL-015/016/020/043…) — thứ judge không thể biết vì không đọc ledger → chuyển sang grader code `reply_matches_ledger` (blocking, FM-08). Phần còn lại (xưng hô) đã do tiêu chí tone phụ trách.
- **tone_ok dùng được** (κ 0.757, P 0.857, R 0.80 ≥ floors) nhưng chỉ để *theo dõi* chất lượng giọng văn, **không chặn release**: không bao giờ chấm tiền/approval bằng judge; 6 fn còn lại đều là lỗi xưng hô → sửa rubric như #1–#3 rồi calibrate lại trên nhãn mới.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai (100 dòng, cùng phân phối với chỗ ta sửa rubric) — cần ≥100 nhãn người **mới** làm held-out thật trước khi tin số κ sau khi sửa rubric.
