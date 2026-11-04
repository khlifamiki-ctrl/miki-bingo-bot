# 🎱 Miki Bingo (ሚኪ ቢንጎ) - የቦት አዘገጃጀት እና ማስጀመሪያ መመሪያ

ይህ መመሪያ **Miki Bingo (@miki_bingo_bot)** የተሰኘውን የቴሌግራም ቦት እና ሚኒ አፕ እንዴት ኦንላይን እንደሚያስጀምሩ ደረጃ በደረጃ ያብራራል።

---

## 1. የፋይሎች ዝርዝር (Files Included)
1. **`miki_bingo_bot.py`**: የቴሌግራም ቦት እና የዌብ አፕ ሰርቨር (Python 3.10+)
2. **`index.html`**: ራሱን የቻለ የቴሌግራም ሚኒ አፕ ገጽ (HTML5 + CSS + JS)
3. **`requirements.txt`**: የሚያስፈልጉ የፓይዘን ላይብረሪዎች

---

## 2. የተስተካከሉ የክፍያ አካውንቶች (Official Payment Accounts)
* **TeleBirr (ሞባይል ገንዘብ):** `0967063387` (ስም: `mikiya`)
* **CBE Birr (ባንክ ማስተላለፍ):** `1000586434814` (ስም: `mikiya`)
* **M-Pesa (ሞባይል ገንዘብ):** `0967063387` (ስም: `mikiya`)
* **E-Birr (ሞባይል ገንዘብ):** `0967063387` (ስም: `mikiya`)

---

## 3. ቦቱን በ @BotFather የማገናኘት ቅደም-ተከተል

### ደረጃ 1፡ ቦቱን መፍጠር
1. በቴሌግራም **[@BotFather](https://t.me/BotFather)** ይፈልጉና `/start` ይበሉ።
2. `/newbot` ብለው ይላኩ።
3. ስም ይጠይቅዎታል፦ `Miki Bingo` ይበሉ።
4. Username ይጠይቅዎታል፦ `miki_bingo_bot` ይበሉ።
5. BotFather የሚሰጥዎትን **HTTP API TOKEN** (ረጅም ቁጥርና ፊደል) ይቅዱ።

### ደረጃ 2፡ ሚኒ አፑን ማገናኘት (Mini App)
1. @BotFather ላይ `/newapp` ይላኩ።
2. የፈጠሩትን `@miki_bingo_bot` ይምረጡ።
3. የዌብ አፑን ስም `Miki Bingo` ይበሉ።
4. አጭር ማብራሪያ ይጻፉ (ለምሳሌ: *የቀጥታ ቢንጎ ይጫወቱ፣ ብር ያሸንፉ!*)።
5. የካርድ ፎቶ (640x360 ፒክሰል) ይላኩለት።
6. **Web App URL** ሲጠይቅዎ `index.html` የተጫነበትን ድረ-ገጽ አድራሻ (GitHub Pages ወይም Railway URL) ያስገቡለት።

### ደረጃ 3፡ የሜኑ ቁልፍ (Menu Button) ማድረግ
1. @BotFather ላይ `/setmenubutton` ይላኩ።
2. ቦቱን ከመረጡ በኋላ የ Mini App ሊንክዎን አስገብተው አርእስቱን **«🎱 Play»** ይበሉት።

### ደረጃ 4፡ 9ኙን ትዕዛዞች በ @BotFather መመዝገብ (/setcommands)
1. @BotFather ላይ `/setcommands` ይላኩ።
2. `@miki_bingo_bot` ይምረጡ።
3. የሚከተለውን ሙሉውን ኮፒ አድርገው ይላኩለት፦
```text
bingo - 🎯 የቀጥታ ቢንጎ ቁጥር መሳቢያ
spin - 🎡 የእድል መንኮራኩር ማሽከርከሪያ
dice - 🎲 የዳይስ ጨዋታ
jackpot - 💎 የ 800 ሳንቲም ሜጋ ጃክፖት
balance - 💰 ቀሪ ሂሳብዎን ይመልከቱ
top - 🏆 ሳምንታዊ ከፍተኛ ተጫዋቾች
daily - 🎁 የ 24 ሰዓት ነጻ የቦነስ ሳንቲም
invite - 👥 ጓደኛ በመጋበዝ 100 ሳንቲም ያግኙ
live - 🎱 የቀጥታ ዙር መረጃ
slots - 🎰 777 Slots ማሽን
rules - 📖 የቢንጎ አጨዋወት መመሪያ
deposit - 📥 ገንዘብ ገቢ ማድረጊያ
withdraw - 📤 ያሸነፉትን ገንዘብ ማውጫ
```

---

## 4. ቦቱን በ Railway ላይ በነጻ ማስጀመር (Deploy on Railway)

1. ወደ [railway.app](https://railway.app) ይግቡ።
2. **New Project** -> **Deploy from GitHub Repo** ይምረጡ።
3. ፋይሎቹን (`miki_bingo_bot.py`, `index.html`, `requirements.txt`) ወደ GitHub ሬፖዚተሪዎ ይጫኑ።
4. በ Railway የ **Variables** ክፍል ውስጥ የሚከተሉትን ያስገቡ፦
   - `TOKEN` = `ከ BotFather ያገኙት የቦት ቶከን`
   - `BOT_USERNAME` = `miki_bingo_bot`
   - `ADMIN_IDS` = `የእርስዎ የቴሌግራም ID ቁጥር`
5. በ **Start Command** ላይ የሚከተለውን ያስገቡ፦
   ```bash
   python miki_bingo_bot.py
   ```
6. **Deploy** ሲሉ ቦቱ እና ሚኒ አፑ 24 ሰዓት በቋሚነት መስራት ይጀምራሉ!
