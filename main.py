import flet as ft
import json
import os
import sys
import shutil
import threading
import urllib.parse
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# ReportLab pro tvorbu PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

DATA_FILE = "zakazky.json"
SETTINGS_FILE = "nastaveni.json"

DEFAULT_SETTINGS = {
    "company_name": "",
    "street": "",
    "city": "",
    "ico": "",
    "bank_account_1": "",
    "bank_label_1": "Účet 1 (Hlavní)",
    "bank_account_2": "",
    "bank_label_2": "Účet 2 (Vedlejší)",
    "last_invoice_number": 20260001,
    "logo_path": "assets/logo.png",
    "use_logo": True
}

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                res = DEFAULT_SETTINGS.copy()
                res.update(data)
                return res
        except Exception:
            return DEFAULT_SETTINGS.copy()
    return DEFAULT_SETTINGS.copy()

def save_settings(data):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_output_folder():
    out_dir = os.path.abspath("faktury")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir

def find_logo_file():
    possible = [
        "assets/logo.png",
        "logo.png",
        os.path.join(os.path.dirname(__file__), "assets", "logo.png"),
        os.path.join(os.path.dirname(__file__), "logo.png"),
        os.path.join(os.path.abspath("."), "assets", "logo.png"),
        os.path.join(os.path.abspath("."), "logo.png")
    ]
    for p in possible:
        if os.path.exists(p) and os.path.isfile(p):
            return p
    return None

def parse_cz_date(date_str):
    try:
        return datetime.strptime((date_str or "").strip(), "%d.%m.%Y").date()
    except Exception:
        return None

def setup_fonts():
    font_paths = [
        "/system/fonts/Roboto-Regular.ttf",
        "/system/fonts/DroidSans.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    font_bold_paths = [
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/DroidSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]

    font_name = "Helvetica"
    font_bold_name = "Helvetica-Bold"

    for p in font_paths:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont("CzechFont", p))
                font_name = "CzechFont"
                break
            except Exception:
                pass

    for p in font_bold_paths:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont("CzechFont-Bold", p))
                font_bold_name = "CzechFont-Bold"
                break
            except Exception:
                pass

    if font_bold_name == "Helvetica-Bold" and font_name != "Helvetica":
        font_bold_name = font_name

    return font_name, font_bold_name

def main(page: ft.Page):
    page.title = "Servis & Detailing 4.0"
    page.theme_mode = ft.ThemeMode.DARK
    page.scroll = ft.ScrollMode.AUTO
    page.padding = 15

    db = load_data()
    settings = load_settings()

    # Stav navigace:
    # Úroveň 1: screen_home (Seznam zákazníků)
    # Úroveň 2: screen_customer (Vozidla daného zákazníka)
    # Úroveň 3: screen_car (Doklady daného vozu)
    # Úroveň 4: screen_order (Detail dokladu / položky)
    # Nastavení: screen_settings
    selected_customer_norm = None
    selected_car_norm = None
    current_key = None
    is_loading_order = False
    only_overdue_filter = False

    BORDER_CLR = "#5a6978"
    BG_INPUT = "#1a1f29"

    def norm_name(val):
        return " ".join((val or "").strip().lower().split())

    def display_car_label(val):
        cleaned = " ".join((val or "").strip().split())
        return cleaned if cleaned else "Bez uvedeného vozu"

    def exit_app_click(e):
        try:
            page.window.close()
        except Exception:
            pass
        os._exit(0)

    def get_next_invoice_number():
        max_num = int(settings.get("last_invoice_number", 20260000))
        for ord_val in db.values():
            if ord_val.get("doc_type", "invoice") == "invoice":
                try:
                    n = int(str(ord_val.get("invoice_number", "")).strip())
                    if n > max_num:
                        max_num = n
                except ValueError:
                    pass
        return max_num + 1

    # --- POTVRZOVACÍ DIALOG PRO SMAZÁNÍ ---
    dlg_confirm_delete = ft.AlertDialog(
        modal=True,
        title=ft.Text("Potvrdit smazání"),
        content=ft.Text("Opravdu chcete položku smazat?"),
        actions=[]
    )
    page.overlay.append(dlg_confirm_delete)

    def ask_delete_order(order_key, after_delete_cb=None):
        cust = db.get(order_key, {}).get("customer", "vybraný doklad")
        car = db.get(order_key, {}).get("car", "")
        inv = db.get(order_key, {}).get("invoice_number", "")
        info = f" ({car})" if car else ""
        if inv:
            info += f" – č. {inv}"

        def do_delete(e):
            if order_key in db:
                del db[order_key]
                save_data(db)
                if after_delete_cb:
                    after_delete_cb()
            dlg_confirm_delete.open = False
            page.update()

        def cancel_delete(e):
            dlg_confirm_delete.open = False
            page.update()

        dlg_confirm_delete.title = ft.Text("Smazat doklad?", weight=ft.FontWeight.BOLD)
        dlg_confirm_delete.content = ft.Text(f"Opravdu chcete trvale smazat doklad pro:\n{cust}{info}?")
        dlg_confirm_delete.actions = [
            ft.TextButton("Zrušit", on_click=cancel_delete),
            ft.Button(content=ft.Text("Ano, smazat", color="redaccent"), on_click=do_delete)
        ]
        dlg_confirm_delete.open = True
        page.update()

    def ask_delete_item(index):
        if not current_key or current_key not in db:
            return
        items = db[current_key].get("items", [])
        if not (0 <= index < len(items)):
            return

        item = items[index]
        item_name = item.get("name", "položku")
        tot = int(item["total"]) if float(item["total"]).is_integer() else item["total"]

        def do_delete_item(e):
            if current_key and current_key in db:
                cur_items = db[current_key].get("items", [])
                if 0 <= index < len(cur_items):
                    cur_items.pop(index)
                    save_data(db)
                    render_order_items()
            dlg_confirm_delete.open = False
            page.update()

        def cancel_delete_item(e):
            dlg_confirm_delete.open = False
            page.update()

        dlg_confirm_delete.title = ft.Text("Smazat položku?", weight=ft.FontWeight.BOLD)
        dlg_confirm_delete.content = ft.Text(f"Opravdu chcete smazat položku:\n„{item_name}“ ({tot:,} Kč)?".replace(",", " "))
        dlg_confirm_delete.actions = [
            ft.TextButton("Zrušit", on_click=cancel_delete_item),
            ft.Button(content=ft.Text("Ano, smazat", color="redaccent"), on_click=do_delete_item)
        ]
        dlg_confirm_delete.open = True
        page.update()

    # --- PRVKY NASTAVENÍ FIRMY ---
    txt_set_company = ft.TextField(label="Název firmy / Jméno", value=settings.get("company_name", ""), bgcolor=BG_INPUT, border_color=BORDER_CLR, border_radius=10)
    txt_set_street = ft.TextField(label="Ulice a č.p.", value=settings.get("street", ""), bgcolor=BG_INPUT, border_color=BORDER_CLR, border_radius=10)
    txt_set_city = ft.TextField(label="Město a PSČ", value=settings.get("city", ""), bgcolor=BG_INPUT, border_color=BORDER_CLR, border_radius=10)
    txt_set_ico = ft.TextField(label="IČ", value=settings.get("ico", ""), keyboard_type=ft.KeyboardType.NUMBER, bgcolor=BG_INPUT, border_color=BORDER_CLR, border_radius=10)
    
    txt_set_bank_label_1 = ft.TextField(label="Popis 1. účtu (např. Hlavní)", value=settings.get("bank_label_1", "Účet 1"), bgcolor=BG_INPUT, border_color=BORDER_CLR, border_radius=10)
    txt_set_bank_1 = ft.TextField(label="Číslo 1. účtu (např. 123456789/0800)", value=settings.get("bank_account_1", ""), bgcolor=BG_INPUT, border_color=BORDER_CLR, border_radius=10)
    
    txt_set_bank_label_2 = ft.TextField(label="Popis 2. účtu (např. Vedlejší)", value=settings.get("bank_label_2", "Účet 2"), bgcolor=BG_INPUT, border_color=BORDER_CLR, border_radius=10)
    txt_set_bank_2 = ft.TextField(label="Číslo 2. účtu (volitelné)", value=settings.get("bank_account_2", ""), bgcolor=BG_INPUT, border_color=BORDER_CLR, border_radius=10)

    txt_set_last_inv = ft.TextField(label="Poslední číslo faktury", value=str(settings.get("last_invoice_number", 20260001)), keyboard_type=ft.KeyboardType.NUMBER, bgcolor=BG_INPUT, border_color=BORDER_CLR, border_radius=10)
    
    chk_use_logo = ft.Checkbox(label="Tisknout logo na fakturu", value=settings.get("use_logo", True))
    lbl_set_saved = ft.Text("", color="greenaccent", italic=True)

    def save_settings_click(e):
        try:
            last_inv = int(txt_set_last_inv.value.strip())
        except ValueError:
            last_inv = 20260001

        settings["company_name"] = txt_set_company.value.strip()
        settings["street"] = txt_set_street.value.strip()
        settings["city"] = txt_set_city.value.strip()
        settings["ico"] = txt_set_ico.value.strip()
        settings["bank_label_1"] = txt_set_bank_label_1.value.strip() or "Účet 1"
        settings["bank_account_1"] = txt_set_bank_1.value.strip()
        settings["bank_label_2"] = txt_set_bank_label_2.value.strip() or "Účet 2"
        settings["bank_account_2"] = txt_set_bank_2.value.strip()
        settings["last_invoice_number"] = last_inv
        settings["use_logo"] = chk_use_logo.value
        
        save_settings(settings)
        update_bank_dropdown()
        lbl_set_saved.value = "✓ Nastavení bylo úspěšně uloženo"
        page.update()

    def hide_all_screens():
        screen_home.visible = False
        screen_customer.visible = False
        screen_car.visible = False
        screen_order.visible = False
        screen_settings.visible = False

    def open_settings_click(e):
        lbl_set_saved.value = ""
        hide_all_screens()
        screen_settings.visible = True
        page.update()

    def close_settings_click(e):
        hide_all_screens()
        screen_home.visible = True
        refresh_customers_list(txt_search.value or "")

    screen_settings = ft.Column([
        ft.Row([
            ft.Text("Nastavení firmy", size=22, weight=ft.FontWeight.BOLD, color="blueaccent"),
            ft.Button(content=ft.Text("← Zpět"), on_click=close_settings_click)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(height=10),
        
        ft.Text("Logo na dokladech:", weight=ft.FontWeight.BOLD),
        chk_use_logo,
        ft.Text("Logo se automaticky načítá ze souboru assets/logo.png", italic=True, color="grey400", size=12),
        ft.Divider(height=10),

        txt_set_company,
        txt_set_street,
        txt_set_city,
        txt_set_ico,
        ft.Divider(height=10),
        ft.Text("Bankovní účty pro faktury:", weight=ft.FontWeight.BOLD),
        txt_set_bank_label_1,
        txt_set_bank_1,
        txt_set_bank_label_2,
        txt_set_bank_2,
        ft.Text("Poznámka: Na faktuře bude automaticky 'Neplátce DPH'.", italic=True, color="grey400", size=13),
        ft.Divider(height=10),
        ft.Text("Číslování faktur:", weight=ft.FontWeight.BOLD),
        txt_set_last_inv,
        ft.Button(content=ft.Text("Uložit nastavení", size=15), on_click=save_settings_click, height=45),
        lbl_set_saved
    ], spacing=10, visible=False)

    # --- STAV DOKLADU (VČETNĚ "BEZ POLOŽEK") ---
    def get_order_status_info(order):
        """Vrací (stav_kod, text_stitku, barva_textu, barva_okraje)"""
        items = order.get("items", [])
        if not items:
            return ("empty", "⚪ Bez položek", "grey400", "#3a4352")

        doc_type = order.get("doc_type", "invoice")
        if doc_type == "quote":
            return ("quote", "🔵 Cenová nabídka (rozpočet)", "lightskyblue", "#2e4a66")

        if order.get("is_paid", False):
            return ("paid", "🟢 Zaplaceno", "greenaccent", "#2b543b")

        today = datetime.now().date()
        due_dt = parse_cz_date(order.get("due_date", ""))
        if due_dt:
            diff = (due_dt - today).days
            if diff < 0:
                days_over = abs(diff)
                d_word = "den" if days_over == 1 else ("dny" if 2 <= days_over <= 4 else "dní")
                return ("overdue", f"🔴 PO SPLATNOSTI o {days_over} {d_word} ({order.get('due_date')})", "redaccent", "redaccent")
            elif diff == 0:
                return ("due_today", f"🟠 Splatnost DNES ({order.get('due_date')})", "orangeaccent", "orangeaccent")
            else:
                d_word = "den" if diff == 1 else ("dny" if 2 <= diff <= 4 else "dní")
                return ("unpaid", f"🟡 Čeká na úhradu — za {diff} {d_word} ({order.get('due_date')})", "amberaccent", "#6b5a2b")

        return ("unpaid", "🟡 Nezaplaceno", "amberaccent", "#6b5a2b")

    # --- ÚVODNÍ OBRAZOVKA (ÚROVEŇ 1: ZÁKAZNÍCI) ---
    overdue_banner = ft.Container(
        visible=False,
        padding=12,
        border_radius=10,
        bgcolor="#3b181d",
        border=ft.Border(
            top=ft.BorderSide(1, "redaccent"),
            bottom=ft.BorderSide(1, "redaccent"),
            left=ft.BorderSide(1, "redaccent"),
            right=ft.BorderSide(1, "redaccent"),
        )
    )

    rg_new_doc_type = ft.RadioGroup(
        content=ft.Row([
            ft.Radio(value="invoice", label="Ostrá faktura"),
            ft.Radio(value="quote", label="Cenová nabídka (rozpočet)"),
        ]),
        value="invoice"
    )

    txt_new_customer = ft.TextField(label="Jméno zákazníka / Název firmy (povinné)", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)
    txt_new_car = ft.TextField(label="Typ vozu (např. Audi, BMW, VW)", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)
    txt_new_job_title = ft.TextField(label="Stručný popis opravy (např. Brzdy + olej)", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)
    txt_new_invoice_num = ft.TextField(label="Číslo faktury", value=str(get_next_invoice_number()), keyboard_type=ft.KeyboardType.NUMBER, border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)

    def on_new_doc_type_changed(e):
        txt_new_invoice_num.visible = (rg_new_doc_type.value == "invoice")
        if rg_new_doc_type.value == "invoice" and not txt_new_invoice_num.value.strip():
            txt_new_invoice_num.value = str(get_next_invoice_number())
        page.update()

    rg_new_doc_type.on_change = on_new_doc_type_changed

    txt_search = ft.TextField(label="Hledat zákazníka, vůz, opravu nebo č. faktury...", prefix_icon="search", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)
    customers_list_view = ft.Column(spacing=10)

    # --- DRUHÁ OBRAZOVKA (ÚROVEŇ 2: VOZIDLA DANÉHO ZÁKAZNÍKA) ---
    lbl_cust_screen_title = ft.Text(size=22, weight=ft.FontWeight.BOLD, color="blueaccent")
    lbl_cust_screen_info = ft.Text(size=13, color="grey400")
    cars_list_view = ft.Column(spacing=10)

    # --- TŘETÍ OBRAZOVKA (ÚROVEŇ 3: DOKLADY DANÉHO VOZU) ---
    lbl_car_screen_title = ft.Text(size=21, weight=ft.FontWeight.BOLD, color="blueaccent")
    lbl_car_screen_sub = ft.Text(size=13, color="grey400")
    car_orders_list_view = ft.Column(spacing=10)

    # --- ČTVRTÁ OBRAZOVKA (ÚROVEŇ 4: DETAIL DOKLADU) ---
    lbl_order_title = ft.Text(size=20, weight=ft.FontWeight.BOLD, color="blueaccent")
    lbl_order_meta = ft.Text(size=14, italic=True, color="grey400")

    rg_order_doc_type = ft.RadioGroup(
        content=ft.Row([
            ft.Radio(value="invoice", label="Ostrá faktura"),
            ft.Radio(value="quote", label="Cenová nabídka"),
        ]),
        value="invoice"
    )

    txt_order_job_title = ft.TextField(
        label="Název / popis zakázky (např. Výměna rozvodů)",
        border_radius=10,
        border_color=BORDER_CLR,
        bgcolor=BG_INPUT
    )

    txt_order_inv_num = ft.TextField(
        label="Číslo faktury (lze opravit)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_radius=10,
        border_color=BORDER_CLR,
        bgcolor=BG_INPUT
    )

    rg_cust_type = ft.RadioGroup(
        content=ft.Row([
            ft.Radio(value="person", label="Fyzická osoba"),
            ft.Radio(value="company", label="Firma / IČ"),
        ]),
        value="person"
    )

    txt_order_cust_street = ft.TextField(label="Ulice a č.p.", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)
    txt_order_cust_city = ft.TextField(label="Město a PSČ", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)
    txt_order_cust_ico = ft.TextField(label="Rodné číslo (RČ)", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)

    rg_payment_method = ft.RadioGroup(
        content=ft.Row([
            ft.Radio(value="transfer", label="Převodem"),
            ft.Radio(value="cash", label="Hotově"),
        ]),
        value="transfer"
    )

    chk_order_paid = ft.Checkbox(label="✓ Doklad je ZAPLACEN", value=False)

    txt_order_due_date = ft.TextField(
        label="Datum splatnosti",
        hint_text="DD.MM.RRRR",
        expand=True,
        border_radius=10,
        border_color=BORDER_CLR,
        bgcolor=BG_INPUT
    )

    def update_order_meta_header(order):
        doc_type = order.get("doc_type", "invoice")
        inv_no = order.get("invoice_number", "")
        car = order.get("car", "")
        date_str = order.get("date", "")
        meta = []
        if doc_type == "quote":
            meta.append("CENOVÁ NABÍDKA (Rozpočet)")
        elif inv_no:
            meta.append(f"Faktura č. {inv_no}")
        if car:
            meta.append(f"Vůz: {car}")
        if date_str:
            meta.append(f"Datum: {date_str}")
        lbl_order_meta.value = " | ".join(meta)

    def validate_and_fix_due_date(order, enforce_fix=False):
        doc_date_str = order.get("date", datetime.now().strftime("%d.%m.%Y"))
        doc_dt = parse_cz_date(doc_date_str) or datetime.now().date()
        raw_due = (txt_order_due_date.value or "").strip()
        due_dt = parse_cz_date(raw_due)

        if not raw_due:
            txt_order_due_date.error_text = None
            if enforce_fix:
                fixed = (doc_dt + timedelta(days=14)).strftime("%d.%m.%Y")
                txt_order_due_date.value = fixed
                return fixed
            return ""

        if due_dt is None:
            txt_order_due_date.error_text = "Zadejte datum ve formátu DD.MM.RRRR"
            if enforce_fix:
                fixed = (doc_dt + timedelta(days=14)).strftime("%d.%m.%Y")
                txt_order_due_date.value = fixed
                txt_order_due_date.error_text = None
                return fixed
            return raw_due

        if due_dt < doc_dt:
            txt_order_due_date.error_text = f"Splatnost nesmí být dříve než vystavení ({doc_date_str})"
            if enforce_fix:
                fixed = doc_date_str
                txt_order_due_date.value = fixed
                txt_order_due_date.error_text = None
                return fixed
            return raw_due

        txt_order_due_date.error_text = None
        return raw_due

    def save_current_cust_fields(enforce_due_fix=False):
        if is_loading_order or not current_key or current_key not in db:
            return
        order = db[current_key]
        new_inv = txt_order_inv_num.value.strip()
        valid_due = validate_and_fix_due_date(order, enforce_fix=enforce_due_fix)

        order["doc_type"] = rg_order_doc_type.value
        order["job_title"] = txt_order_job_title.value.strip()
        order["invoice_number"] = new_inv
        order["cust_type"] = rg_cust_type.value
        order["street"] = txt_order_cust_street.value.strip()
        order["city"] = txt_order_cust_city.value.strip()
        order["ico_rc"] = txt_order_cust_ico.value.strip()
        order["payment_method"] = rg_payment_method.value
        order["is_paid"] = bool(chk_order_paid.value)
        order["due_date"] = valid_due
        save_data(db)
        update_order_meta_header(order)

    def apply_due_offset(days_count):
        if not current_key or current_key not in db:
            return
        order = db[current_key]
        doc_dt = parse_cz_date(order.get("date", "")) or datetime.now().date()
        new_due = (doc_dt + timedelta(days=days_count)).strftime("%d.%m.%Y")
        txt_order_due_date.value = new_due
        save_current_cust_fields(enforce_due_fix=True)
        page.update()

    def on_date_picker_change(e):
        if not e.control.value or not current_key or current_key not in db:
            return
        picked = e.control.value
        if isinstance(picked, datetime):
            picked_date = picked.date()
        else:
            picked_date = picked
        order = db[current_key]
        doc_dt = parse_cz_date(order.get("date", "")) or datetime.now().date()
        if picked_date < doc_dt:
            picked_date = doc_dt
        txt_order_due_date.value = picked_date.strftime("%d.%m.%Y")
        save_current_cust_fields(enforce_due_fix=True)
        page.update()

    due_date_picker = ft.DatePicker(
        first_date=datetime(2020, 1, 1),
        last_date=datetime(2035, 12, 31),
        on_change=on_date_picker_change,
    )
    page.overlay.append(due_date_picker)

    def open_due_calendar_click(e):
        if current_key and current_key in db:
            cur_dt = parse_cz_date(txt_order_due_date.value or "") or datetime.now().date()
            due_date_picker.value = datetime(cur_dt.year, cur_dt.month, cur_dt.day)
        due_date_picker.open = True
        page.update()

    dd_order_bank = ft.Dropdown(
        label="Bankovní účet na fakturu",
        border_radius=10,
        border_color=BORDER_CLR,
        bgcolor=BG_INPUT
    )

    payment_section_col = ft.Column([
        ft.Text("Platební podmínky, splatnost a úhrada:", weight=ft.FontWeight.BOLD, color="grey400", size=13),
        rg_payment_method,
        chk_order_paid,
        ft.Row([
            txt_order_due_date,
            ft.Button(
                content=ft.Row([ft.Icon(ft.Icons.CALENDAR_MONTH, size=18), ft.Text("Kalendář")], spacing=4),
                on_click=open_due_calendar_click,
                height=50
            )
        ], spacing=6),
        ft.Row([
            ft.Button(content=ft.Text("+7 dní", size=12), on_click=lambda e: apply_due_offset(7), expand=True, height=34),
            ft.Button(content=ft.Text("+14 dní", size=12), on_click=lambda e: apply_due_offset(14), expand=True, height=34),
            ft.Button(content=ft.Text("+30 dní", size=12), on_click=lambda e: apply_due_offset(30), expand=True, height=34),
        ], spacing=6),
        dd_order_bank,
        ft.Divider(height=10),
    ], spacing=8)

    def on_order_doc_type_changed(e):
        is_inv = (rg_order_doc_type.value == "invoice")
        txt_order_inv_num.visible = is_inv
        payment_section_col.visible = is_inv
        btn_generate_pdf.content = ft.Text("🖨️ Vytvořit PDF fakturu" if is_inv else "🖨️ PDF Cenová nabídka", size=15)

        if is_inv and not txt_order_inv_num.value.strip():
            next_num = get_next_invoice_number()
            txt_order_inv_num.value = str(next_num)
            settings["last_invoice_number"] = next_num
            save_settings(settings)
            txt_set_last_inv.value = str(next_num)

        if not is_loading_order:
            save_current_cust_fields()
        page.update()

    def on_order_inv_changed(e):
        if not is_loading_order:
            save_current_cust_fields()
            page.update()

    def on_cust_type_changed(e):
        txt_order_cust_ico.label = "IČ firmy" if rg_cust_type.value == "company" else "Rodné číslo (RČ)"
        if not is_loading_order:
            save_current_cust_fields()
        page.update()

    def on_payment_method_changed(e):
        is_transfer = (rg_payment_method.value == "transfer")
        dd_order_bank.visible = is_transfer
        if not is_transfer and not is_loading_order:
            chk_order_paid.value = True
        if not is_loading_order:
            save_current_cust_fields()
        page.update()

    def on_paid_changed(e):
        if not is_loading_order:
            save_current_cust_fields()
        page.update()

    def on_due_date_changed(e):
        if not is_loading_order:
            save_current_cust_fields(enforce_due_fix=False)
            page.update()

    def on_due_date_blur(e):
        if not is_loading_order:
            save_current_cust_fields(enforce_due_fix=True)
            page.update()

    rg_order_doc_type.on_change = on_order_doc_type_changed
    txt_order_job_title.on_change = lambda e: save_current_cust_fields()
    txt_order_inv_num.on_change = on_order_inv_changed
    rg_cust_type.on_change = on_cust_type_changed
    rg_payment_method.on_change = on_payment_method_changed
    chk_order_paid.on_change = on_paid_changed
    txt_order_cust_street.on_change = lambda e: save_current_cust_fields()
    txt_order_cust_city.on_change = lambda e: save_current_cust_fields()
    txt_order_cust_ico.on_change = lambda e: save_current_cust_fields()
    txt_order_due_date.on_change = on_due_date_changed
    txt_order_due_date.on_blur = on_due_date_blur

    def update_bank_dropdown():
        opts = []
        b1 = settings.get("bank_account_1", "")
        lbl1 = settings.get("bank_label_1", "Účet 1")
        if b1:
            opts.append(ft.dropdown.Option("acc1", f"{lbl1}: {b1}"))
            
        b2 = settings.get("bank_account_2", "")
        lbl2 = settings.get("bank_label_2", "Účet 2")
        if b2:
            opts.append(ft.dropdown.Option("acc2", f"{lbl2}: {b2}"))

        if not opts:
            opts.append(ft.dropdown.Option("none", "Žádný účet nenastaven"))

        dd_order_bank.options = opts
        if not dd_order_bank.value or dd_order_bank.value not in [o.key for o in opts]:
            dd_order_bank.value = opts[0].key

    update_bank_dropdown()

    def on_order_bank_change(e):
        if not is_loading_order and current_key and current_key in db:
            db[current_key]["selected_bank"] = dd_order_bank.value
            save_data(db)

    dd_order_bank.on_change = on_order_bank_change

    dd_item_type = ft.Dropdown(
        label="Typ položky",
        options=[
            ft.dropdown.Option("work", "🛠️ Práce (účtováno na hodiny)"),
            ft.dropdown.Option("material", "📦 Materiál a náhradní díly"),
        ],
        value="work",
        border_radius=10,
        border_color=BORDER_CLR,
        bgcolor=BG_INPUT
    )

    txt_item_name = ft.TextField(label="Popis práce (např. Výměna oleje)", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)
    txt_item_qty = ft.TextField(label="Počet hodin", value="1", keyboard_type=ft.KeyboardType.NUMBER, expand=2, border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)

    dd_unit = ft.Dropdown(
        label="Jedn.",
        options=[
            ft.dropdown.Option("hod"),
            ft.dropdown.Option("ks"),
            ft.dropdown.Option("kg"),
            ft.dropdown.Option("l"),
            ft.dropdown.Option("g"),
            ft.dropdown.Option("sada"),
            ft.dropdown.Option("bal")
        ],
        value="hod",
        expand=2,
        border_radius=10,
        border_color=BORDER_CLR,
        bgcolor=BG_INPUT
    )
    
    txt_item_price = ft.TextField(label="Hodinová sazba v Kč", keyboard_type=ft.KeyboardType.NUMBER, border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)

    def on_item_type_changed(e):
        if dd_item_type.value == "work":
            txt_item_name.label = "Popis práce (např. Výměna oleje a filtrů)"
            txt_item_qty.label = "Počet hodin"
            dd_unit.value = "hod"
            txt_item_price.label = "Hodinová sazba v Kč"
        else:
            txt_item_name.label = "Název materiálu / dílu (např. Barva, Olej)"
            txt_item_qty.label = "Množství"
            if dd_unit.value == "hod":
                dd_unit.value = "ks"
            txt_item_price.label = "Cena za jednotku v Kč"
        page.update()

    dd_item_type.on_change = on_item_type_changed

    items_work_view = ft.Column(spacing=6)
    items_material_view = ft.Column(spacing=6)
    lbl_total_price = ft.Text("Celkem: 0 Kč", size=22, weight=ft.FontWeight.BOLD, color="greenaccent")

    btn_generate_pdf = ft.Button(content=ft.Text("🖨️ Vytvořit PDF fakturu", size=15), expand=True, height=50)
    wa_box = ft.Container(visible=False, padding=5)
    invoice_print_box = ft.Container(visible=False, padding=5)

    # --- POMOCNÉ FUNKCE PRO HIERARCHII ZÁKAZNÍK -> VŮZ -> DOKLADY ---
    def get_customer_profile(cust_norm):
        """Najde nejnovější údaje profilu zákazníka (adresa, IČ/RČ, platba)."""
        for _, order in reversed(list(db.items())):
            if norm_name(order.get("customer")) == cust_norm:
                return {
                    "customer": order.get("customer", ""),
                    "cust_type": order.get("cust_type", "person"),
                    "street": order.get("street", ""),
                    "city": order.get("city", ""),
                    "ico_rc": order.get("ico_rc", ""),
                    "payment_method": order.get("payment_method", "transfer"),
                    "selected_bank": order.get("selected_bank", dd_order_bank.value),
                }
        return {
            "customer": cust_norm,
            "cust_type": "person",
            "street": "",
            "city": "",
            "ico_rc": "",
            "payment_method": "transfer",
            "selected_bank": dd_order_bank.value,
        }

    def create_doc_for_customer_and_car(cust_name, car_name="", doc_type="invoice", job_title="", inv_number_str=None):
        c_norm = norm_name(cust_name)
        profile = get_customer_profile(c_norm)
        clean_cust = cust_name.strip() or profile.get("customer") or "Zákazník"
        clean_car = (car_name or "").strip()

        now = datetime.now()
        key = f"{clean_cust}_{now.strftime('%Y%m%d%H%M%S%f')}"

        if doc_type == "invoice":
            if not inv_number_str:
                inv_number_str = str(get_next_invoice_number())
            try:
                parsed_inv = int(inv_number_str)
                if parsed_inv >= settings.get("last_invoice_number", 0):
                    settings["last_invoice_number"] = parsed_inv
                    save_settings(settings)
                    txt_set_last_inv.value = str(parsed_inv)
            except ValueError:
                pass
        else:
            inv_number_str = ""

        db[key] = {
            "doc_type": doc_type,
            "customer": clean_cust,
            "car": clean_car,
            "job_title": (job_title or "").strip(),
            "invoice_number": inv_number_str,
            "date": now.strftime("%d.%m.%Y"),
            "due_date": (now + timedelta(days=14)).strftime("%d.%m.%Y"),
            "payment_method": profile.get("payment_method", "transfer"),
            "is_paid": False,
            "selected_bank": profile.get("selected_bank", dd_order_bank.value),
            "cust_type": profile.get("cust_type", "person"),
            "street": profile.get("street", ""),
            "city": profile.get("city", ""),
            "ico_rc": profile.get("ico_rc", ""),
            "items": []
        }
        save_data(db)
        txt_new_invoice_num.value = str(get_next_invoice_number())
        open_order(key)

    # Dialog pro přidání nového vozu pod stávajícího zákazníka
    txt_add_car_name = ft.TextField(label="Název vozu (např. Audi A4, BMW X5, VW Golf)", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)
    txt_add_car_job = ft.TextField(label="První oprava / zakázka (volitelné)", border_radius=10, border_color=BORDER_CLR, bgcolor=BG_INPUT)

    dlg_add_car = ft.AlertDialog(
        modal=True,
        title=ft.Text("Přidat vůz zákazníkovi", weight=ft.FontWeight.BOLD),
        content=ft.Column([txt_add_car_name, txt_add_car_job], tight=True, spacing=10),
        actions=[]
    )
    page.overlay.append(dlg_add_car)

    def open_add_car_dialog(e):
        if not selected_customer_norm:
            return
        prof = get_customer_profile(selected_customer_norm)
        txt_add_car_name.value = ""
        txt_add_car_job.value = ""

        def confirm_add_car(ev):
            car_val = (txt_add_car_name.value or "").strip()
            if not car_val:
                txt_add_car_name.error_text = "Zadejte název vozu"
                page.update()
                return
            txt_add_car_name.error_text = None
            dlg_add_car.open = False
            page.update()
            create_doc_for_customer_and_car(
                cust_name=prof.get("customer", ""),
                car_name=car_val,
                doc_type="invoice",
                job_title=(txt_add_car_job.value or "").strip()
            )

        def cancel_add_car(ev):
            txt_add_car_name.error_text = None
            dlg_add_car.open = False
            page.update()

        dlg_add_car.actions = [
            ft.TextButton("Zrušit", on_click=cancel_add_car),
            ft.Button(content=ft.Text("Vytvořit vůz a doklad"), on_click=confirm_add_car)
        ]
        dlg_add_car.open = True
        page.update()

    # --- ÚROVEŇ 1: REFRESH ZÁKAZNÍKŮ ---
    def toggle_overdue_filter(e):
        nonlocal only_overdue_filter
        only_overdue_filter = not only_overdue_filter
        refresh_customers_list(txt_search.value or "")

    def refresh_customers_list(filter_text=""):
        customers_list_view.controls.clear()
        query = filter_text.lower().strip()

        # 1. Souhrn faktur po splatnosti (pouze u dokladů s položkami)
        overdue_count = 0
        overdue_sum = 0.0
        for ord_item in db.values():
            st_code, _, _, _ = get_order_status_info(ord_item)
            if st_code == "overdue":
                overdue_count += 1
                overdue_sum += sum(i["total"] for i in ord_item.get("items", []))

        if overdue_count > 0:
            f_word = "faktura" if overdue_count == 1 else ("faktury" if 2 <= overdue_count <= 4 else "faktur")
            btn_lbl = "Zobrazit všechny zákazníky" if only_overdue_filter else "Zobrazit jen po splatnosti"
            overdue_banner.content = ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color="redaccent"),
                    ft.Text(
                        f"Po splatnosti: {overdue_count} {f_word} ({int(overdue_sum):,} Kč)".replace(",", " "),
                        weight=ft.FontWeight.BOLD,
                        color="redaccent",
                        size=15,
                        expand=True
                    )
                ]),
                ft.Button(content=ft.Text(btn_lbl, size=13), on_click=toggle_overdue_filter, height=36)
            ], spacing=6)
            overdue_banner.visible = True
        else:
            overdue_banner.visible = False
            only_overdue_filter = False

        # 2. Seskupení dokladů podle jména zákazníka (od nejnovějšího)
        grouped = {}
        order_of_customers = []
        for key, order in reversed(list(db.items())):
            c_norm = norm_name(order.get("customer"))
            if not c_norm:
                c_norm = key
            if c_norm not in grouped:
                grouped[c_norm] = {
                    "display_name": order.get("customer", "Zákazník").strip() or "Zákazník",
                    "orders": []
                }
                order_of_customers.append(c_norm)
            grouped[c_norm]["orders"].append((key, order))

        shown = 0
        for c_norm in order_of_customers:
            grp = grouped[c_norm]
            c_orders = grp["orders"]
            c_name = grp["display_name"]

            # Filtr po splatnosti
            has_overdue = any(get_order_status_info(o)[0] == "overdue" for _, o in c_orders)
            if only_overdue_filter and not has_overdue:
                continue

            # Textové vyhledávání přes jméno zákazníka, jeho vozy, opravy i čísla faktur
            if query:
                matches_cust = query in c_name.lower()
                matches_any_order = any(
                    query in (o.get("car") or "").lower()
                    or query in (o.get("job_title") or "").lower()
                    or query in str(o.get("invoice_number") or "").lower()
                    for _, o in c_orders
                )
                if not matches_cust and not matches_any_order:
                    continue

            if not query and not only_overdue_filter and shown >= 40:
                continue

            shown += 1

            # Seznam unikátních vozů zákazníka
            cars_seen = []
            for _, o in c_orders:
                cl = display_car_label(o.get("car", ""))
                if cl not in cars_seen:
                    cars_seen.append(cl)

            total_all = sum(sum(i["total"] for i in o.get("items", [])) for _, o in c_orders)
            unpaid_sum = sum(
                sum(i["total"] for i in o.get("items", []))
                for _, o in c_orders
                if get_order_status_info(o)[0] in ("overdue", "unpaid", "due_today")
            )

            if has_overdue:
                badge_text = "🔴 Má fakturu po splatnosti"
                badge_color = "redaccent"
                border_col = "redaccent"
            elif unpaid_sum > 0:
                badge_text = f"🟡 K úhradě: {int(unpaid_sum):,} Kč".replace(",", " ")
                badge_color = "amberaccent"
                border_col = "#6b5a2b"
            else:
                badge_text = "🟢 Vše v pořádku"
                badge_color = "greenaccent"
                border_col = "#2e3846"

            cars_preview = ", ".join(cars_seen[:4]) + ("..." if len(cars_seen) > 4 else "")
            info_line = f"Vozy ({len(cars_seen)}): {cars_preview}\nDokladů: {len(c_orders)} | Celkem obrat: {int(total_all):,} Kč".replace(",", " ")

            card = ft.Container(
                content=ft.Column([
                    ft.Text(c_name, weight=ft.FontWeight.BOLD, size=17),
                    ft.Text(badge_text, color=badge_color, size=12, weight=ft.FontWeight.BOLD),
                    ft.Text(info_line, color="grey400", size=13),
                    ft.Button(
                        content=ft.Text("Otevřít zákazníka (vozy a doklady)", weight=ft.FontWeight.BOLD),
                        on_click=lambda _, cn=c_norm: open_customer(cn),
                        width=float("inf"),
                        height=40
                    )
                ], spacing=6),
                padding=12,
                border_radius=10,
                border=ft.Border(
                    top=ft.BorderSide(1, border_col),
                    bottom=ft.BorderSide(1, border_col),
                    left=ft.BorderSide(1, border_col),
                    right=ft.BorderSide(1, border_col),
                ),
                bgcolor="#1e222d"
            )
            customers_list_view.controls.append(card)

        if shown == 0:
            customers_list_view.controls.append(ft.Text("Žádní zákazníci k zobrazení.", italic=True, color="grey500"))
        page.update()

    # --- ÚROVEŇ 2: KARTA ZÁKAZNÍKA -> PODSLOŽKY PODLE VOZU ---
    def open_customer(cust_norm):
        nonlocal selected_customer_norm
        selected_customer_norm = cust_norm
        refresh_customer_cars()
        hide_all_screens()
        screen_customer.visible = True
        page.update()

    def refresh_customer_cars():
        cars_list_view.controls.clear()
        if not selected_customer_norm:
            return

        cust_orders = [(k, o) for k, o in reversed(list(db.items())) if norm_name(o.get("customer")) == selected_customer_norm]
        if not cust_orders:
            back_from_customer_to_home(None)
            return

        prof = get_customer_profile(selected_customer_norm)
        lbl_cust_screen_title.value = f"Zákazník: {prof.get('customer')}"

        addr_parts = [p for p in [prof.get("street", ""), prof.get("city", "")] if p]
        info_bits = []
        if addr_parts:
            info_bits.append(", ".join(addr_parts))
        if prof.get("ico_rc"):
            lbl_id = "IČ" if prof.get("cust_type") == "company" else "RČ"
            info_bits.append(f"{lbl_id}: {prof.get('ico_rc')}")
        lbl_cust_screen_info.value = " | ".join(info_bits) if info_bits else "Vyberte vůz pro zobrazení jeho dokladů"

        # Seskupení podle vozu
        cars_map = {}
        cars_order = []
        for k, o in cust_orders:
            c_lbl = display_car_label(o.get("car", ""))
            c_key = norm_name(c_lbl)
            if c_key not in cars_map:
                cars_map[c_key] = {"label": c_lbl, "raw_car": (o.get("car") or "").strip(), "orders": []}
                cars_order.append(c_key)
            cars_map[c_key]["orders"].append((k, o))

        for c_key in cars_order:
            c_data = cars_map[c_key]
            c_lbl = c_data["label"]
            c_raw = c_data["raw_car"]
            c_ords = c_data["orders"]

            has_over = any(get_order_status_info(o)[0] == "overdue" for _, o in c_ords)
            has_unpaid = any(get_order_status_info(o)[0] in ("unpaid", "due_today") for _, o in c_ords)
            car_total = sum(sum(i["total"] for i in o.get("items", [])) for _, o in c_ords)

            if has_over:
                st_txt = "🔴 Má doklad po splatnosti"
                st_col = "redaccent"
                b_col = "redaccent"
            elif has_unpaid:
                st_txt = "🟡 Čeká na úhradu"
                st_col = "amberaccent"
                b_col = "#6b5a2b"
            else:
                st_txt = "📁 Podsložka vozu"
                st_col = "lightskyblue"
                b_col = "#2e3846"

            car_card = ft.Container(
                content=ft.Column([
                    ft.Text(f"🚗 {c_lbl}", weight=ft.FontWeight.BOLD, size=17),
                    ft.Text(st_txt, color=st_col, size=12, weight=ft.FontWeight.BOLD),
                    ft.Text(f"Dokladů: {len(c_ords)} | Celkem: {int(car_total):,} Kč".replace(",", " "), color="grey400", size=13),
                    ft.Row([
                        ft.Button(
                            content=ft.Text("Otevřít doklady vozu", weight=ft.FontWeight.BOLD),
                            on_click=lambda _, ck=c_key: open_car(ck),
                            expand=True,
                            height=38
                        ),
                        ft.Button(
                            content=ft.Text("+ Nový doklad", size=13),
                            on_click=lambda _, cn=prof.get("customer", ""), cr=c_raw: create_doc_for_customer_and_car(cn, cr, "invoice"),
                            height=38
                        )
                    ], spacing=8)
                ], spacing=6),
                padding=12,
                border_radius=10,
                border=ft.Border(
                    top=ft.BorderSide(1, b_col),
                    bottom=ft.BorderSide(1, b_col),
                    left=ft.BorderSide(1, b_col),
                    right=ft.BorderSide(1, b_col),
                ),
                bgcolor="#1e222d"
            )
            cars_list_view.controls.append(car_card)

    def back_from_customer_to_home(e):
        nonlocal selected_customer_norm
        selected_customer_norm = None
        hide_all_screens()
        screen_home.visible = True
        refresh_customers_list(txt_search.value or "")

    # --- ÚROVEŇ 3: DOKLADY DANÉHO VOZU ---
    def open_car(car_key):
        nonlocal selected_car_norm
        selected_car_norm = car_key
        refresh_car_orders()
        hide_all_screens()
        screen_car.visible = True
        page.update()

    def refresh_car_orders():
        car_orders_list_view.controls.clear()
        if not selected_customer_norm or not selected_car_norm:
            return

        matching = [
            (k, o) for k, o in reversed(list(db.items()))
            if norm_name(o.get("customer")) == selected_customer_norm
            and norm_name(display_car_label(o.get("car", ""))) == selected_car_norm
        ]

        if not matching:
            back_from_car_to_customer(None)
            return

        prof = get_customer_profile(selected_customer_norm)
        first_order = matching[0][1]
        car_display = display_car_label(first_order.get("car", ""))
        raw_car = (first_order.get("car") or "").strip()

        lbl_car_screen_title.value = f"{prof.get('customer')} — {car_display}"
        lbl_car_screen_sub.value = "Jednotlivé faktury a cenové nabídky tohoto vozu"

        for key, order in matching:
            st_code, st_label, st_color, border_color = get_order_status_info(order)
            items = order.get("items", [])
            total = sum(i["total"] for i in items)
            doc_type = order.get("doc_type", "invoice")
            inv_num = str(order.get("invoice_number", ""))
            job_title = order.get("job_title", "")
            date_str = order.get("date", "")
            due_str = order.get("due_date", "")

            line1_parts = []
            if doc_type == "quote":
                line1_parts.append("Cenová nabídka")
            elif inv_num:
                line1_parts.append(f"Faktura č. {inv_num}")
            else:
                line1_parts.append("Doklad")
            if date_str:
                line1_parts.append(f"Vystaveno: {date_str}")
            if items and doc_type != "quote" and due_str:
                line1_parts.append(f"Splatnost: {due_str}")

            sub = " | ".join(line1_parts)
            if job_title:
                sub += f"\nPopis: {job_title}"
            if not items:
                sub += "\nPoložek: 0 (bez položek)"
            else:
                sub += f"\nPoložek: {len(items)} | Celkem: {int(total):,} Kč".replace(",", " ")

            doc_card = ft.Container(
                content=ft.Column([
                    ft.Text(st_label, color=st_color, size=13, weight=ft.FontWeight.BOLD),
                    ft.Text(sub, color="grey400", size=13),
                    ft.Row([
                        ft.Button(
                            content=ft.Text("Otevřít doklad", weight=ft.FontWeight.BOLD),
                            on_click=lambda _, k=key: open_order(k),
                            expand=True,
                            height=38
                        ),
                        ft.Button(
                            content=ft.Text("Smazat", color="redaccent", size=13),
                            on_click=lambda _, k=key: ask_delete_order(k, after_delete_cb=refresh_car_orders),
                            height=38
                        )
                    ], spacing=8)
                ], spacing=6),
                padding=12,
                border_radius=10,
                border=ft.Border(
                    top=ft.BorderSide(1, border_color),
                    bottom=ft.BorderSide(1, border_color),
                    left=ft.BorderSide(1, border_color),
                    right=ft.BorderSide(1, border_color),
                ),
                bgcolor="#1e222d"
            )
            car_orders_list_view.controls.append(doc_card)

        btn_new_doc_for_car.on_click = lambda e, cn=prof.get("customer", ""), cr=raw_car: create_doc_for_customer_and_car(cn, cr, "invoice")

    def back_from_car_to_customer(e):
        nonlocal selected_car_norm
        selected_car_norm = None
        refresh_customer_cars()
        hide_all_screens()
        screen_customer.visible = True
        page.update()

    # --- ÚROVEŇ 4: DETAIL DOKLADU ---
    def open_order(order_key):
        nonlocal current_key, is_loading_order, selected_customer_norm, selected_car_norm
        is_loading_order = True
        try:
            current_key = order_key
            order = db[current_key]
            selected_customer_norm = norm_name(order.get("customer")) or current_key
            selected_car_norm = norm_name(display_car_label(order.get("car", "")))

            lbl_order_title.value = f"Zákazník: {order.get('customer')}"
            update_order_meta_header(order)

            doc_type = order.get("doc_type", "invoice")
            rg_order_doc_type.value = doc_type
            txt_order_job_title.value = order.get("job_title", "")
            txt_order_inv_num.value = str(order.get("invoice_number", ""))

            is_inv = (doc_type == "invoice")
            txt_order_inv_num.visible = is_inv
            payment_section_col.visible = is_inv
            btn_generate_pdf.content = ft.Text("🖨️ Vytvořit PDF fakturu" if is_inv else "🖨️ PDF Cenová nabídka", size=15)

            rg_cust_type.value = order.get("cust_type", "person")
            txt_order_cust_ico.label = "IČ firmy" if rg_cust_type.value == "company" else "Rodné číslo (RČ)"
            txt_order_cust_street.value = order.get("street", "")
            txt_order_cust_city.value = order.get("city", "")
            txt_order_cust_ico.value = order.get("ico_rc", "")

            rg_payment_method.value = order.get("payment_method", "transfer")
            chk_order_paid.value = bool(order.get("is_paid", False))
            doc_dt = parse_cz_date(order.get("date", "")) or datetime.now().date()
            default_due = (doc_dt + timedelta(days=14)).strftime("%d.%m.%Y")
            saved_due = (order.get("due_date") or "").strip()
            if not saved_due:
                saved_due = default_due
                order["due_date"] = saved_due
                save_data(db)
            txt_order_due_date.value = saved_due
            validate_and_fix_due_date(order, enforce_fix=False)
            
            update_bank_dropdown()
            saved_bank = order.get("selected_bank")
            if saved_bank and saved_bank in [o.key for o in dd_order_bank.options]:
                dd_order_bank.value = saved_bank

            dd_order_bank.visible = (rg_payment_method.value == "transfer")

            dd_item_type.value = "work"
            txt_item_name.label = "Popis práce (např. Výměna oleje a filtrů)"
            txt_item_qty.label = "Počet hodin"
            dd_unit.value = "hod"
            txt_item_price.label = "Hodinová sazba v Kč"
            txt_item_name.value = ""
            txt_item_qty.value = "1"
            txt_item_price.value = ""
            wa_box.visible = False
            invoice_print_box.visible = False
            
            hide_all_screens()
            screen_order.visible = True
        finally:
            is_loading_order = False
        render_order_items()

    def render_order_items():
        items_work_view.controls.clear()
        items_material_view.controls.clear()
        if not current_key or current_key not in db:
            return

        items = db[current_key].get("items", [])
        total = 0.0

        work_items = []
        material_items = []

        for idx, item in enumerate(items):
            item_type = item.get("type")
            if not item_type:
                item_type = "work" if item.get("unit") == "hod" else "material"
            
            if item_type == "work":
                work_items.append((idx, item))
            else:
                material_items.append((idx, item))

        if work_items:
            for idx, item in work_items:
                total += item["total"]
                q = int(item["qty"]) if float(item["qty"]).is_integer() else item["qty"]
                p = int(item["price"]) if float(item["price"]).is_integer() else item["price"]
                tot = int(item["total"]) if float(item["total"]).is_integer() else item["total"]

                items_work_view.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Column([
                                ft.Text(f"🛠️ {item['name']}", weight=ft.FontWeight.BOLD, size=15),
                                ft.Text(f"{q} hod x {p} Kč/hod = {tot:,} Kč".replace(",", " "), color="grey400", size=13),
                            ], expand=True),
                            ft.Button(content=ft.Text("✕", color="redaccent", size=16), on_click=lambda _, i=idx: ask_delete_item(i))
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        padding=10,
                        border_radius=10,
                        border=ft.Border(
                            top=ft.BorderSide(1, "#2e3846"),
                            bottom=ft.BorderSide(1, "#2e3846"),
                            left=ft.BorderSide(1, "#2e3846"),
                            right=ft.BorderSide(1, "#2e3846"),
                        ),
                        bgcolor="#1e222d"
                    )
                )
        else:
            items_work_view.controls.append(ft.Text("Žádná práce zatím nezadána.", italic=True, color="grey500", size=13))

        if material_items:
            for idx, item in material_items:
                total += item["total"]
                q = int(item["qty"]) if float(item["qty"]).is_integer() else item["qty"]
                p = int(item["price"]) if float(item["price"]).is_integer() else item["price"]
                tot = int(item["total"]) if float(item["total"]).is_integer() else item["total"]

                items_material_view.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Column([
                                ft.Text(f"📦 {item['name']}", weight=ft.FontWeight.BOLD, size=15),
                                ft.Text(f"{q} {item.get('unit', 'ks')} x {p} Kč = {tot:,} Kč".replace(",", " "), color="grey400", size=13),
                            ], expand=True),
                            ft.Button(content=ft.Text("✕", color="redaccent", size=16), on_click=lambda _, i=idx: ask_delete_item(i))
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        padding=10,
                        border_radius=10,
                        border=ft.Border(
                            top=ft.BorderSide(1, "#2e3846"),
                            bottom=ft.BorderSide(1, "#2e3846"),
                            left=ft.BorderSide(1, "#2e3846"),
                            right=ft.BorderSide(1, "#2e3846"),
                        ),
                        bgcolor="#1e222d"
                    )
                )
        else:
            items_material_view.controls.append(ft.Text("Žádný materiál zatím nezadán.", italic=True, color="grey500", size=13))

        lbl_total_price.value = f"Celkem: {int(total) if total.is_integer() else total:,} Kč".replace(",", " ")
        page.update()

    def add_item_click(e):
        if not current_key or current_key not in db:
            return
        if not txt_item_name.value or not txt_item_price.value:
            return
        
        try:
            qty = float(txt_item_qty.value.replace(",", "."))
            price = float(txt_item_price.value.replace(",", "."))
        except ValueError:
            return

        item_total = qty * price
        item_type = dd_item_type.value
        unit = "hod" if item_type == "work" else dd_unit.value

        new_item = {
            "type": item_type,
            "name": txt_item_name.value.strip(),
            "qty": qty,
            "unit": unit,
            "price": price,
            "total": item_total
        }
        
        db[current_key]["items"].append(new_item)
        save_data(db)

        txt_item_name.value = ""
        txt_item_qty.value = "1"
        txt_item_price.value = ""
        
        render_order_items()
        txt_item_name.focus()

    def create_order_click(e):
        if not txt_new_customer.value or not txt_new_customer.value.strip():
            txt_new_customer.error_text = "Zadejte jméno zákazníka"
            page.update()
            return
        
        txt_new_customer.error_text = None
        doc_type = rg_new_doc_type.value
        cust_name = txt_new_customer.value.strip()
        car_name = txt_new_car.value.strip() if txt_new_car.value else ""
        job_title = txt_new_job_title.value.strip() if txt_new_job_title.value else ""
        inv_number_str = txt_new_invoice_num.value.strip() if doc_type == "invoice" else ""

        txt_new_customer.value = ""
        txt_new_car.value = ""
        txt_new_job_title.value = ""

        create_doc_for_customer_and_car(
            cust_name=cust_name,
            car_name=car_name,
            doc_type=doc_type,
            job_title=job_title,
            inv_number_str=inv_number_str
        )

    def back_from_order_to_car(e):
        nonlocal current_key
        save_current_cust_fields(enforce_due_fix=True)
        current_key = None
        wa_box.visible = False
        invoice_print_box.visible = False
        refresh_car_orders()
        hide_all_screens()
        screen_car.visible = True
        page.update()

    def get_active_account_details(order):
        chosen = order.get("selected_bank", "acc1")
        if chosen == "acc2" and settings.get("bank_account_2"):
            return settings.get("bank_account_2")
        return settings.get("bank_account_1", "")

    # --- ZPRÁVA NA WHATSAPP ---
    def share_receipt_click(e):
        if not current_key or current_key not in db:
            return
        
        save_current_cust_fields(enforce_due_fix=True)
        order = db[current_key]
        items = order.get("items", [])
        if not items:
            return

        is_quote = (order.get("doc_type") == "quote")
        is_cash = (order.get("payment_method") == "cash")
        pay_text = "Hotově" if is_cash else "Převodem"
        due_str = order.get("due_date", "")

        if is_quote:
            receipt = "Dobrý den, posílám předběžnou cenovou nabídku (rozpočet) na servis Vašeho vozu.\n\n"
        else:
            receipt = "Dobrý den, posílám vyúčtování za servis Vašeho vozu.\n\n"

        receipt += f"*Zákazník:* {order.get('customer')}\n"
        
        addr_parts = []
        if order.get("street"):
            addr_parts.append(order.get("street"))
        if order.get("city"):
            addr_parts.append(order.get("city"))
        if addr_parts:
            receipt += f"*Adresa:* {', '.join(addr_parts)}\n"
            
        if order.get("ico_rc"):
            lbl_ico = "IČ" if order.get("cust_type") == "company" else "RČ"
            receipt += f"*{lbl_ico}:* {order.get('ico_rc')}\n"
            
        if order.get("car"):
            receipt += f"*Vozidlo:* {order.get('car')}\n"
        if order.get("job_title"):
            receipt += f"*Předmět opravy:* {order.get('job_title')}\n"

        if not is_quote:
            receipt += f"*Forma úhrady:* {pay_text}\n"
            if due_str:
                receipt += f"*Datum splatnosti:* {due_str}\n"
            if not is_cash:
                acc = get_active_account_details(order)
                if acc:
                    receipt += f"*Číslo účtu:* {acc}\n"
            
        receipt += "\n*Soupis prací a materiálu:*\n"
        receipt += "================================\n"

        work_items = [i for i in items if i.get("type") == "work" or (not i.get("type") and i.get("unit") == "hod")]
        mat_items = [i for i in items if i.get("type") == "material" or (not i.get("type") and i.get("unit") != "hod")]

        total_all = sum(i["total"] for i in items)

        if work_items:
            receipt += "\n🛠️ *PRÁCE:*\n"
            sub_w = 0
            for it in work_items:
                q = int(it['qty']) if float(it['qty']).is_integer() else it['qty']
                p = int(it['price']) if float(it['price']).is_integer() else it['price']
                tot = int(it['total']) if float(it['total']).is_integer() else it['total']
                receipt += f"  • {it['name']}: {q} hod x {p} Kč = *{tot:,} Kč*\n".replace(",", " ")
                sub_w += it['total']
            receipt += f"  _Mezisoučet práce: {int(sub_w):,} Kč_\n".replace(",", " ")

        if mat_items:
            receipt += "\n📦 *MATERIÁL A DÍLY:*\n"
            sub_m = 0
            for it in mat_items:
                q = int(it['qty']) if float(it['qty']).is_integer() else it['qty']
                p = int(it['price']) if float(it['price']).is_integer() else it['price']
                tot = int(it['total']) if float(it['total']).is_integer() else it['total']
                receipt += f"  • {it['name']}: {q} {it.get('unit', 'ks')} x {p} Kč = *{tot:,} Kč*\n".replace(",", " ")
                sub_m += it['total']
            receipt += f"  _Mezisoučet materiálu: {int(sub_m):,} Kč_\n".replace(",", " ")

        receipt += "================================\n"
        lbl_tot = "ODHADOVANÁ CENA CELKEM" if is_quote else "CELKEM K ÚHRADĚ"
        receipt += f"*{lbl_tot}: {int(total_all):,} Kč*\n\n".replace(",", " ")
        receipt += "Děkujeme za důvěru!"

        encoded_message = urllib.parse.quote(receipt)
        wa_app_url = f"whatsapp://send?text={encoded_message}"
        
        wa_box.content = ft.Column([
            ft.Text("Zpráva připravena:", color="greenaccent", size=14, weight=ft.FontWeight.BOLD),
            ft.Button(content=ft.Text("👉 Otevřít WhatsApp se zprávou", size=15), url=wa_app_url, height=50),
            ft.TextField(
                label="Nebo zkopírujte text přímo odsud:",
                value=receipt,
                multiline=True,
                min_lines=4,
                max_lines=8,
                read_only=True,
                border_radius=10,
                border_color=BORDER_CLR,
                bgcolor=BG_INPUT
            )
        ], spacing=8)
        wa_box.visible = True
        invoice_print_box.visible = False
        page.update()

    # --- GENEROVÁNÍ PDF ---
    def generate_invoice_pdf_click(e):
        if not current_key or current_key not in db:
            return

        save_current_cust_fields(enforce_due_fix=True)
        order = db[current_key]
        items = order.get("items", [])
        if not items:
            return

        is_quote = (order.get("doc_type") == "quote")
        inv_no = order.get("invoice_number", "DOKLAD")
        doc_date = order.get("date", datetime.now().strftime("%d.%m.%Y"))
        due_date = order.get("due_date", doc_date)
        is_cash = (order.get("payment_method") == "cash")
        pay_label = "Hotově" if is_cash else "Převodem"
        total_all = sum(i["total"] for i in items)

        out_folder = get_output_folder()
        if is_quote:
            safe_cust = "".join(c for c in order.get("customer", "zakaznik") if c.isalnum() or c in (" ", "-", "_")).strip().replace(" ", "_")
            pdf_filename = f"nabidka_{safe_cust}_{datetime.now().strftime('%Y%m%d')}.pdf"
            doc_heading = "CENOVÁ NABÍDKA (Rozpočet)"
        else:
            pdf_filename = f"faktura_{inv_no}.pdf"
            doc_heading = f"FAKTURA č. {inv_no}"

        pdf_path = os.path.join(out_folder, pdf_filename)

        work_items = [i for i in items if i.get("type") == "work" or (not i.get("type") and i.get("unit") == "hod")]
        mat_items = [i for i in items if i.get("type") == "material" or (not i.get("type") and i.get("unit") != "hod")]

        acc_num = get_active_account_details(order)
        font_name, font_bold_name = setup_fonts()

        doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
        elements = []

        title_style = ParagraphStyle(
            name='InvoiceTitle',
            fontName=font_bold_name,
            fontSize=19 if is_quote else 20,
            leading=24,
            textColor=colors.HexColor("#1a202c"),
            spaceAfter=15
        )
        body_style = ParagraphStyle(name='CzechBody', fontName=font_name, fontSize=10, leading=14)
        body_bold = ParagraphStyle(name='CzechBodyBold', fontName=font_bold_name, fontSize=10, leading=14)
        total_title_style = ParagraphStyle(name='TotTitle', fontName=font_bold_name, fontSize=11, leading=15, alignment=2)
        total_val_style = ParagraphStyle(name='TotVal', fontName=font_bold_name, fontSize=12, leading=15, alignment=2, textColor=colors.HexColor("#0d5ca8"))

        logo_file = find_logo_file() if settings.get("use_logo", True) else None
        if logo_file:
            try:
                logo_img = RLImage(logo_file, width=90, height=90)
                top_data = [
                    [Paragraph(doc_heading, title_style), logo_img]
                ]
                t_top = Table(top_data, colWidths=[410, 110])
                t_top.setStyle(TableStyle([
                    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                    ('ALIGN', (1,0), (1,0), 'RIGHT'),
                ]))
                elements.append(t_top)
            except Exception:
                elements.append(Paragraph(doc_heading, title_style))
        else:
            elements.append(Paragraph(doc_heading, title_style))

        elements.append(Spacer(1, 10))

        supp_lines = [
            f"<b>Dodavatel:</b>",
            f"{settings.get('company_name', 'Servis')}",
            f"{settings.get('street', '')}",
            f"{settings.get('city', '')}",
            f"IČ: {settings.get('ico', 'Neuvedeno')}"
        ]
        if not is_quote and not is_cash and acc_num:
            supp_lines.append(f"Účet: <b>{acc_num}</b>")
        supp_text = "<br/>".join(supp_lines)

        cust_lines = [
            f"<b>Odběratel:</b>",
            f"<b>{order.get('customer')}</b>"
        ]
        if order.get("street"):
            cust_lines.append(order.get("street"))
        if order.get("city"):
            cust_lines.append(order.get("city"))
            
        if order.get("ico_rc"):
            lbl_doc = "IČ" if order.get("cust_type") == "company" else "RČ"
            cust_lines.append(f"{lbl_doc}: {order.get('ico_rc')}")
            
        if order.get("car"):
            cust_lines.append(f"Vozidlo: {order.get('car')}")
        if order.get("job_title"):
            cust_lines.append(f"Předmět: {order.get('job_title')}")

        cust_lines.append(f"<br/>Datum vystavení: {doc_date}")
        if not is_quote:
            if due_date:
                cust_lines.append(f"Datum splatnosti: <b>{due_date}</b>")
            cust_lines.append(f"Forma úhrady: <b>{pay_label}</b>")
        cust_text = "<br/>".join(cust_lines)

        header_data = [[Paragraph(supp_text, body_style), Paragraph(cust_text, body_style)]]
        t_header = Table(header_data, colWidths=[270, 250])
        t_header.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LINEBELOW', (0,0), (-1,-1), 1, colors.HexColor("#4a5568")),
            ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ]))
        elements.append(t_header)
        elements.append(Spacer(1, 15))

        if work_items:
            elements.append(Paragraph("<b>Práce:</b>", body_bold))
            elements.append(Spacer(1, 4))
            w_data = [[Paragraph("Popis úkonu", body_bold), Paragraph("Rozsah", body_bold), Paragraph("Sazba", body_bold), Paragraph("Celkem", body_bold)]]
            for it in work_items:
                q = int(it['qty']) if float(it['qty']).is_integer() else it['qty']
                p = int(it['price']) if float(it['price']).is_integer() else it['price']
                tot = int(it['total']) if float(it['total']).is_integer() else it['total']
                w_data.append([
                    Paragraph(it['name'], body_style),
                    Paragraph(f"{q} hod", body_style),
                    Paragraph(f"{p} Kč", body_style),
                    Paragraph(f"{tot:,} Kč".replace(",", " "), body_style)
                ])

            t_work = Table(w_data, colWidths=[260, 80, 80, 100])
            t_work.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#edf2f7")),
                ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e0")),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            elements.append(t_work)
            elements.append(Spacer(1, 12))

        if mat_items:
            elements.append(Paragraph("<b>Materiál a náhradní díly:</b>", body_bold))
            elements.append(Spacer(1, 4))
            m_data = [[Paragraph("Položka", body_bold), Paragraph("Množství", body_bold), Paragraph("Cena/j.", body_bold), Paragraph("Celkem", body_bold)]]
            for it in mat_items:
                q = int(it['qty']) if float(it['qty']).is_integer() else it['qty']
                p = int(it['price']) if float(it['price']).is_integer() else it['price']
                tot = int(it['total']) if float(it['total']).is_integer() else it['total']
                m_data.append([
                    Paragraph(it['name'], body_style),
                    Paragraph(f"{q} {it.get('unit', 'ks')}", body_style),
                    Paragraph(f"{p} Kč", body_style),
                    Paragraph(f"{tot:,} Kč".replace(",", " "), body_style)
                ])

            t_mat = Table(m_data, colWidths=[260, 80, 80, 100])
            t_mat.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#edf2f7")),
                ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e0")),
                ('TOPPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ]))
            elements.append(t_mat)
            elements.append(Spacer(1, 10))

        left_note = "<i>Předběžný rozpočet. Dodavatel není plátcem DPH.</i>" if is_quote else "<i>Dodavatel není plátcem DPH.</i>"
        vat_info = Paragraph(left_note, body_style)
        tot_label = Paragraph("ODHADOVANÁ CENA:" if is_quote else "CELKEM K ÚHRADĚ:", total_title_style)
        tot_sum = Paragraph(f"<b>{int(total_all):,} Kč</b>".replace(",", " "), total_val_style)

        t_summary_data = [
            [vat_info, tot_label, tot_sum]
        ]
        t_summary = Table(t_summary_data, colWidths=[200, 220, 100])
        t_summary.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (1,0), (1,0), 'RIGHT'),
            ('ALIGN', (2,0), (2,0), 'RIGHT'),
            ('LINEABOVE', (1,0), (2,0), 1.5, colors.HexColor("#1a202c")),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ]))
        elements.append(t_summary)

        doc.build(elements)

        with open(pdf_path, "rb") as src_f:
            pdf_bytes = src_f.read()

        public_candidates = [
            "/storage/emulated/0/Download",
            "/sdcard/Download",
            "/storage/emulated/0/Documents",
            "/sdcard/Documents"
        ]
        saved_paths = []
        for folder in public_candidates:
            if os.path.exists(folder):
                dest_file = os.path.join(folder, pdf_filename)
                try:
                    with open(dest_file, "wb") as dst_f:
                        dst_f.write(pdf_bytes)
                    saved_paths.append(dest_file)
                    break
                except Exception:
                    pass

        target_path = saved_paths[0] if saved_paths else pdf_path

        class QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=out_folder, **kwargs)
            def log_message(self, format, *args):
                pass

        if not getattr(page, "_pdf_server_port", None):
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
                page._pdf_server_port = srv.server_address[1]
                threading.Thread(target=srv.serve_forever, daemon=True).start()
            except Exception:
                page._pdf_server_port = None

        port = getattr(page, "_pdf_server_port", None)
        pdf_url = f"http://127.0.0.1:{port}/{urllib.parse.quote(pdf_filename)}" if port else f"file://{target_path}"

        created_msg = "✓ PDF cenová nabídka byla vytvořena!" if is_quote else "✓ PDF faktura byla úspěšně vytvořena!"
        sub_msg = f"Nabídka — Celkem: {int(total_all):,} Kč".replace(",", " ") if is_quote else f"Faktura č. {inv_no} — Celkem: {int(total_all):,} Kč ({pay_label})".replace(",", " ")

        invoice_print_box.content = ft.Column([
            ft.Text(created_msg, color="greenaccent", size=16, weight=ft.FontWeight.BOLD),
            ft.Text(sub_msg, size=14),
            ft.Button(
                content=ft.Row([
                    ft.Icon(ft.Icons.PRINT, color="white"),
                    ft.Text("Otevřít PDF pro tisk / zobrazení", size=15, weight=ft.FontWeight.BOLD)
                ], alignment=ft.MainAxisAlignment.CENTER),
                url=pdf_url,
                height=50,
                style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_700, color="white")
            ),
            ft.Text(f"Uloženo: {target_path}", color="grey500", size=11, selectable=True)
        ], spacing=8)
        invoice_print_box.visible = True
        wa_box.visible = False
        page.update()

    btn_generate_pdf.on_click = generate_invoice_pdf_click
    txt_search.on_change = lambda e: refresh_customers_list(txt_search.value)

    # --- OBRAZOVKA 1: SEZNAM ZÁKAZNÍKŮ ---
    screen_home = ft.Column([
        ft.Row([
            ft.Text("Servis & Detailing 4.0", size=24, weight=ft.FontWeight.BOLD, color="blueaccent"),
            ft.Row([
                ft.IconButton(icon=ft.Icons.SETTINGS, icon_color="grey400", tooltip="Nastavení firmy", on_click=open_settings_click),
                ft.IconButton(icon=ft.Icons.POWER_SETTINGS_NEW, icon_color="redaccent", tooltip="Ukončit aplikaci", on_click=exit_app_click)
            ])
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),

        overdue_banner,
        
        ft.Container(
            content=ft.Column([
                ft.Text("Nový doklad / zakázka", weight=ft.FontWeight.BOLD, size=16),
                rg_new_doc_type,
                txt_new_customer,
                txt_new_car,
                txt_new_job_title,
                txt_new_invoice_num,
                ft.Button(content=ft.Text("Založit a otevřít", size=15), on_click=create_order_click, height=45)
            ], spacing=8),
            padding=12,
            border_radius=10,
            border=ft.Border(
                top=ft.BorderSide(1, ft.Colors.GREY_400),
                bottom=ft.BorderSide(1, ft.Colors.GREY_400),
                left=ft.BorderSide(1, ft.Colors.GREY_400),
                right=ft.BorderSide(1, ft.Colors.GREY_400),
            ),
            bgcolor="#161922"
        ),
        
        ft.Divider(height=15, color="transparent"),
        ft.Text("Zákazníci", weight=ft.FontWeight.BOLD, size=16),
        txt_search,
        customers_list_view
    ], spacing=10)

    # --- OBRAZOVKA 2: VOZIDLA DANÉHO ZÁKAZNÍKA ---
    screen_customer = ft.Column([
        ft.Row([
            ft.Column([lbl_cust_screen_title, lbl_cust_screen_info], expand=True),
            ft.Button(content=ft.Text("← Zákazníci"), on_click=back_from_customer_to_home)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Button(
            content=ft.Text("+ Přidat další vůz / doklad zákazníkovi", size=14),
            on_click=open_add_car_dialog,
            height=42
        ),
        ft.Divider(height=10),
        ft.Text("Vozidla zákazníka:", weight=ft.FontWeight.BOLD, size=16),
        cars_list_view
    ], spacing=10, visible=False)

    # --- OBRAZOVKA 3: DOKLADY DANÉHO VOZU ---
    btn_new_doc_for_car = ft.Button(content=ft.Text("+ Nový doklad pro tento vůz", size=14), height=42)

    screen_car = ft.Column([
        ft.Row([
            ft.Column([lbl_car_screen_title, lbl_car_screen_sub], expand=True),
            ft.Button(content=ft.Text("← Vozy"), on_click=back_from_car_to_customer)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        btn_new_doc_for_car,
        ft.Divider(height=10),
        ft.Text("Faktury a cenové nabídky:", weight=ft.FontWeight.BOLD, size=16),
        car_orders_list_view
    ], spacing=10, visible=False)

    # --- OBRAZOVKA 4: DETAIL DOKLADU ---
    screen_order = ft.Column([
        ft.Row([
            ft.Column([lbl_order_title, lbl_order_meta], expand=True),
            ft.Button(content=ft.Text("← Doklady vozu"), on_click=back_from_order_to_car)
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(height=10),

        ft.Text("Typ dokladu a název opravy:", weight=ft.FontWeight.BOLD, color="grey400", size=13),
        rg_order_doc_type,
        txt_order_job_title,
        txt_order_inv_num,
        ft.Divider(height=10),
        
        ft.Text("Typ odběratele a adresa (volitelné):", weight=ft.FontWeight.BOLD, color="grey400", size=13),
        rg_cust_type,
        txt_order_cust_street,
        txt_order_cust_city,
        txt_order_cust_ico,
        ft.Divider(height=10),

        payment_section_col,

        ft.Text("Přidat novou položku:", weight=ft.FontWeight.BOLD, size=16),
        dd_item_type,
        txt_item_name,
        ft.Row([txt_item_qty, dd_unit]),
        txt_item_price,
        ft.Button(content=ft.Text("Vložit do dokladu", size=15), on_click=add_item_click, height=45),
        ft.Divider(height=10),
        
        ft.Text("🛠️ Práce:", weight=ft.FontWeight.BOLD, color="blueaccent"),
        items_work_view,
        
        ft.Divider(height=10),
        ft.Text("📦 Materiál a náhradní díly:", weight=ft.FontWeight.BOLD, color="blueaccent"),
        items_material_view,
        ft.Divider(height=10),
        
        lbl_total_price,
        ft.Divider(height=10, color="transparent"),
        
        ft.Row([
            ft.Button(content=ft.Text("📱 WhatsApp", size=15), on_click=share_receipt_click, expand=True, height=50),
            btn_generate_pdf
        ]),
        wa_box,
        invoice_print_box
    ], spacing=10, visible=False)

    page.add(
        ft.Container(content=screen_home, padding=5),
        ft.Container(content=screen_customer, padding=5),
        ft.Container(content=screen_car, padding=5),
        ft.Container(content=screen_order, padding=5),
        ft.Container(content=screen_settings, padding=5)
    )

    refresh_customers_list()

ft.run(main)