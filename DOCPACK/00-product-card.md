---
title: Book FX Data — Product Card
doc_id: CARD-book-fx-data
doc_type: product-card
verdict: showcase
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: Product owner (Book Dev)
---

# Book FX Data — Product Card

## 1. สรุปคุณค่าในหนึ่งประโยค

ให้อัตราแลกเปลี่ยนที่ตรวจย้อนได้จากแหล่งทางการ (ECB ผ่าน Frankfurter) สำหรับระบบภายใน Solo Empire

## 2. ปัญหาและผู้ใช้เป้าหมาย

- ระบบการเงินส่วนตัวต้องการอัตราแลกเปลี่ยนที่อ้างอิงได้และ replay ได้
- ผู้ใช้: เจ้าของและระบบภายใน; ถูกใช้โดย `book-opportunity-intelligence` (synthesis) และ `solo-empire-insights` (หน้าเว็บ)

## 3. สิ่งที่ทำได้จริงตอนนี้

- `ingest.py` ดึง Frankfurter (ECB) → landing → Bronze → manifest
- `quality.py` ตรวจคุณภาพข้อมูล; `api.py` อ่านผ่าน DuckDB (port 8103)
- test 9 ไฟล์

## 4. หลักฐานสถานะ

- Stack: Python
- Commits: 14 (แรก 2026-08-08 → ล่าสุด 2026-10-01), branch `main`; working tree สะอาด
- Source: 19 ไฟล์ / 2,995 บรรทัด; test 9 ไฟล์
- CI: 1 workflow
- Deploy config: ไม่มี
- Live URL: ไม่พบใน repo
- โครงสร้างไฟล์เหมือนกับ repo อื่นอีก 2 ตัวในตระกูลเดียวกัน (structure signature เดียวกัน)

## 5. คุณค่าและจุดต่าง

- แหล่งข้อมูลทางการและเสถียรที่สุดในกลุ่ม มีประโยชน์จริงกับงานการเงินภายใน
- ไม่มีจุดต่างเชิงตลาด อัตรา ECB หาได้ฟรีทั่วไป

## 6. ช่องว่างสำคัญ

- ไม่มี deploy config หรือ public endpoint; ใช้งานผ่าน API ในเครื่องเท่านั้น (ค่าเริ่มต้น bind 127.0.0.1)
- ยังไม่ยืนยันว่าระบบการเงินของ Solo Empire ใช้ข้อมูลนี้จริง **[ต้องยืนยัน]**

## 7. คำตัดสิน

**showcase** — ทำงานจริง มีคุณภาพข้อมูล แต่เป็นบริการข้อมูลภายในที่ไม่ใช่ธุรกิจ

จะเปลี่ยนเป็น `merge` ถ้ารวม repo ข้อมูลตลาด หรือเป็น `invest` ถ้ากลายเป็นแหล่ง FX หลักของระบบ finance

## 8. ขั้นต่อไป

1. เชื่อมกับ Finance API ของ Solo Empire หากต้องใช้แปลงสกุลเงิน
2. ตัดสินใจรวม 6 repo ข้อมูลตลาด

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Product owner (Book Dev) | รอพิจารณา | — | ________ |

## แหล่งข้อมูล

- `README.md`
- `src/book_fx/ingest.py`
- `src/book_fx/quality.py`
- `src/book_fx/api.py`
- `tests/`
