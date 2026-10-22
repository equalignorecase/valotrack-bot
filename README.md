# ValoTracker (เวอร์ชัน Python)

บอท Discord สำหรับเกม Valorant: เช็คร้านค้าประจำวัน (Daily Shop), ตรวจสอบห้องแข่งขันแบบสด (Live Match) พร้อมรูปภาพ Scoreboard คุณภาพสูง, ตารางจัดอันดับแรงค์แก๊งเพื่อน (Friends Leaderboard), ประวัติการแข่งขัน และกระดานผู้นำระดับภูมิภาค

---

## ฟีเจอร์ทั้งหมด (Features)

### 🛒 ร้านค้าประจำวัน & จัดการบัญชี (Shop & Accounts)
- `/login [name]` - เข้าสู่ระบบด้วยบัญชี Riot ผ่านลิงก์ยืนยันตัวตนทางการของ Riot Games
- `/shop` - ตรวจสอบร้านค้าสกินประจำวันของบัญชีที่เลือกอยู่ พร้อมรูปภาพการ์ดสกิน ระดับความหายาก และราคา VP
- `/view-accounts` - แสดงรายชื่อบัญชี Riot ทั้งหมดที่เชื่อมต่อกับบัญชี Discord ของคุณ
- `/select-account <id>` - สลับบัญชีที่ต้องการใช้งานเป็นบัญชีหลัก (สำหรับผู้ที่มีหลายบัญชี)
- `/delete-account <id>` - ยกเลิกการเชื่อมต่อและลบบัญชี Riot พร้อมโทเคนออกจากฐานข้อมูล
- `/account-list` - *(เฉพาะเจ้าของบอท)* ดูรายชื่อบัญชีทั้งหมดในระบบพร้อมสถานะความถูกต้องของโทเคน

### 👥 ตารางจัดอันดับแรงค์แก๊งเพื่อน (Friends Rank Leaderboard)
- `/checkrank` - ตรวจสอบแรงค์ของแก๊งเพื่อนในเซิร์ฟเวอร์ แสดงผลหน้าละ 5 คน เรียงลำดับตามคะแนน ELO สูงสุด พร้อมปุ่มเปลี่ยนหน้า (`◀️`, `▶️`) และปุ่ม `🔄 อัพเดทแรงค์` แบบเรียลไทม์ (มีระบบ Cooldown ป้องกันสแปม)
- `/addfriend <player>` - เพิ่มเพื่อนเข้าสู่รายชื่อตรวจสอบแรงค์ของเซิร์ฟเวอร์ (`ชื่อ#แท็ก`) โดยบอทจะตรวจสอบบัญชี ดึง PUUID และภูมิภาคให้อัตโนมัติ
- `/removefriend <player>` - ลบเพื่อนออกจากรายชื่อตรวจสอบแรงค์ (มีระบบ Autocomplete ช่วยค้นหาและเลือกชื่อเพื่อนได้ทันที)

### 🎮 สถิติ & ระบบตรวจแมตช์สด (Live Match & Stats)
- `/live [player]` - ตรวจสอบแมตช์ที่กำลังเล่นอยู่ (ทั้งช่วงเลือกเอเจนท์และในเกม) แสดงรูปภาพ Scoreboard สรุปข้อมูลครบถ้วน, ข้อมูลปาร์ตี้ (Stack), แรงค์ปัจจุบัน, แรงค์สูงสุด และไฮไลท์ผู้เล่น
- `/rank <player>` - ดูแรงค์ปัจจุบัน, คะแนน RR, ผลแพ้ชนะแมตช์ล่าสุด (+/- RR) และแรงค์สูงสุดในประวัติศาสตร์ (`ชื่อ#แท็ก`)
- `/lastmatch <player>` - สร้างภาพ Scoreboard สรุปผลแมตช์ล่าสุดแบบละเอียด (รายชื่อทั้งสองทีม, KDA, ACS, Headshot %)
- `/match <player>` - แสดงประวัติการแข่งขัน 5 แมตช์ล่าสุด พร้อมเมนู Dropdown เลือกดูภาพ Scoreboard ของแต่ละเกม
- `/leaderboard [region] [player]` - ดูอันดับ Radiant ของแต่ละภูมิภาค (AP, NA, EU, KR, BR, LATAM) พร้อมระบบเลื่อนหน้า หรือค้นหาชื่อผู้เล่น

### ⚙️ คำสั่งทั่วไป (General)
- `/help` - ดูคู่มือการใช้งานและคำสั่งทั้งหมดของบอท
- `/ping` - ตรวจสอบความหน่วง (Latency) ของบอทและเวลาการทำงานของระบบ (Uptime)

---

## หลักการทำงาน (How It Works)

เนื่องจาก Riot Games ไม่มี Public API สำหรับข้อมูลส่วนตัวอย่างร้านค้าประจำวันหรือห้องเกมสด บอทนี้จึงทำงานร่วมกับ Endpoint ของ Riot Client ควบคู่กับ [HenrikDev Valorant API](https://henrikdev.xyz) และ [valorant-api.com](https://valorant-api.com):

1. **ระบบยืนยันตัวตน (Authentication Flow)**:
   - คำสั่ง `/login` จะสร้างลิงก์สำหรับเข้าสู่ระบบของ Riot อย่างปลอดภัย
   - เมื่อผู้ใช้เข้าสู่ระบบสำเร็จ Riot จะ Redirect ไปยังหน้า `localhost` พร้อมโทเคนยืนยันตัวตนใน URL
   - ผู้ใช้นำ URL นั้นมาวางในกล่องข้อความของบอท
   - บอทจะนำโค้ดไปแลกเป็น Access Token และ Refresh Token ผ่าน `auth.riotgames.com/token`
   - นำ Access Token ไปดึงข้อมูล Entitlement, PUUID และ Region แล้วบันทึกเก็บไว้
   - บอทจะใช้ Refresh Token ต่ออายุ Access Token ให้อัตโนมัติเมื่อหมดอายุ

2. **ความปลอดภัยของข้อมูล (Security)**:
   - บอท **ไม่มีการขอหรือบันทึกรหัสผ่าน** ของผู้ใช้โดยเด็ดขาด
   - ข้อมูล Refresh Token ทั้งหมดจะถูกเข้ารหัสก่อนบันทึกลงฐานข้อมูลด้วยอัลกอริทึม **AES-256-GCM**
   - Access Token มีอายุสั้น (ประมาณ 1 ชั่วโมง)

---

## การติดตั้งและตั้งค่า (Setup & Installation)

### ข้อกำหนดเบื้องต้น
- Python 3.10 ขึ้นไป
- บัญชีและโทเคน Discord Bot ([Discord Developer Portal](https://discord.com/developers/applications))
- HenrikDev Valorant API Key ([henrikdev.xyz](https://henrikdev.xyz))

### การตั้งค่าตัวแปรสภาพแวดล้อม (Configuration)

1. คัดลอกไฟล์ตัวอย่างคอนฟิก:
   ```bash
   cp .env.example .env
   ```

2. สร้างคีย์เข้ารหัส AES ขนาด 32 ไบต์:
   ```bash
   python3 -c "import os, base64; print('AES_KEY=' + base64.b64encode(os.urandom(32)).decode())"
   ```

3. แก้ไขข้อมูลในไฟล์ `.env`:
   ```env
   DISCORD_TOKEN=โทเคนบอทดิสคอร์ดของคุณ
   GUILD_ID=ไอดีเซิร์ฟเวอร์สำหรับทดสอบ     # ไม่บังคับ: ใส่เพื่อให้คำสั่ง Slash อัพเดททันที
   AES_KEY=คีย์_AES_ที่สร้างขึ้น
   HENRIK_API_KEY=คีย์_HenrikDev_API
   DB_PATH=ValoTracker.db
   ```

### วิธีรันบอท (Running the Bot)

**ระบบปฏิบัติการ Windows:**
```cmd
install.bat
run.bat
```
*(หรือติดตั้งเอง: `pip install -r requirements.txt` แล้วรัน `python bot.py`)*

**ระบบปฏิบัติการ macOS / Linux:**
```bash
pip install -r requirements.txt
python3 bot.py
```

---

## ข้อสงวนสิทธิ์ (Disclaimer)

โปรเจกต์นี้เป็นโปรเจกต์ที่ไม่เป็นทางการซึ่งสร้างโดยแฟนเกม และไม่มีความเกี่ยวข้อง ไม่ได้รับการรับรอง หรือสนับสนุนจาก Riot Games, Inc. ทั้งนี้ Valorant และ Riot Games เป็นเครื่องหมายการค้าหรือเครื่องหมายการค้าจดทะเบียนของ Riot Games, Inc.


