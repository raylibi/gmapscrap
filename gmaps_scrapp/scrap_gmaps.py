import sys
import json
import os
import re
import urllib.request
import pandas as pd
import config
from pathlib import Path
from time import sleep
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

COOKIE_FILE = "cookies.json"

def save_cookies(context):
    """Save cookies to a file."""
    cookies = context.cookies()
    with open(COOKIE_FILE, "w") as f:
        json.dump(cookies, f)
    print("Cookies saved!")

def load_cookies(context):
    """Load cookies from a file if it exists."""
    file_path = Path(COOKIE_FILE)
    if file_path.exists():
        with open(COOKIE_FILE, "r") as f:
            cookies = json.load(f)
            context.add_cookies(cookies)
        print("Cookies loaded!")
    else:
        print("No cookies found. Starting fresh session.")

def slow_scroll(page, step=300, delay=1):
    """Scrolls the Google Maps search results panel until reaching the end message."""
    
    scrollable_selector = 'div[role="feed"]'  
    end_message_selector = 'span.HlvSq'  # Selector untuk pesan "Anda telah mencapai akhir daftar."
    
    scrollable_div = page.query_selector(scrollable_selector)
    
    if not scrollable_div:
        print("Scrollable element not found!")
        return

    previous_height = 0

    while True:
        # Cek apakah teks "Anda telah mencapai akhir daftar." muncul
        end_message = page.query_selector(end_message_selector)
        if end_message and "Anda telah mencapai akhir daftar." in end_message.inner_text():
            print("Detected end of list message. Stopping scroll.")
            break

        # Ambil tinggi scroll saat ini
        current_height = page.evaluate(f"document.querySelector('{scrollable_selector}').scrollHeight")

        # Jika tidak ada perubahan tinggi scroll, cek lagi teks "Anda telah mencapai akhir daftar."
        if current_height == previous_height:
            print("No change in scroll height, checking end message again...")
            end_message = page.query_selector(end_message_selector)
            if end_message and "Anda telah mencapai akhir daftar." in end_message.inner_text():
                print("Confirmed end of list message. Stopping scroll.")
                break

        # Scroll ke bawah
        page.evaluate(f"document.querySelector('{scrollable_selector}').scrollBy(0, {step})")
        previous_height = current_height
        print(f"Scrolled to: {previous_height}px")
        
        # Tunggu sebelum scroll berikutnya
        page.wait_for_timeout(delay * 1000)  

    print("Reached the bottom of the results!")

def get_all_links(html_content):
    """Extract all place links from Google Maps search results."""
    links_list = []
    soup = BeautifulSoup(html_content, 'html.parser')
    containers = soup.find_all('a', class_='hfpxzc', href=True)
    for container in containers:
        link = container['href']
        links_list.append(link)
    return links_list 

def get_coordinates(url):
    """Extract latitude and longitude from Google Maps URL."""
    match = re.search(r"3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url)
    if match:
        latitude, longitude = match.groups()
        return latitude, longitude
    return None, None

def is_relevant_travel(name, address):
    """Memeriksa apakah tempat ini merupakan layanan travel atau bukan"""
    
    # Kata kunci yang menunjukkan layanan travel
    travel_keywords = ["travel", "shuttle", "daytrans", "cititrans", "xtrans", "big bird", "primajasa", "pariwisata"]

    # Kata kunci yang menunjukkan layanan yang tidak relevan
    exclude_keywords = ["ojek", "grab", "gojek", "angkot", "ojeg", "maxim", "halte", "ambulance", 
                        "pertigaan", "mabes", "sigesit", "rent", "-republic", "lapangan", "barang", 
                        "paket", "rental", "rumah", "supir", "ojol", "gobox", "jasa", "cargo", "pengiriman",
                        "haji", "umroh", "hajj", "kedai", "laskar", "kios", "indriver", "bc", "basecamp", "teras"]
    
    # Kata kunci lokasi yang harus ada di alamat (agar hanya Bandung)
    location_keywords = ["Bandung", "Kota Bandung", "Gedebage", "Dago", "Pasteur"]

    # Konversi nama dan alamat ke huruf kecil agar pencarian kata kunci tidak case-sensitive
    name_lower = name.lower()
    address_lower = address.lower()

    # Jika ada kata kunci di travel_keywords, langsung anggap relevan
    if any(travel in name_lower for travel in travel_keywords):
        # Pastikan lokasinya di Bandung
        if any(loc.lower() in address_lower for loc in location_keywords):
            return True  # Tetap scrap karena travel di Bandung
        else:
            return False  # Lokasi di luar Bandung, abaikan

    # Jika nama atau alamat mengandung kata kunci yang harus dihindari, beri tanda False
    if any(exclude in name_lower for exclude in exclude_keywords):
        return False  # Data tidak relevan

    # Jika lokasi bukan di Bandung, abaikan
    if not any(loc.lower() in address_lower for loc in location_keywords):
        return False  

    return "irrelevant"

def filter(page):
    """Cek apakah tempat termasuk kategori yang relevan dan tidak tutup sementara."""

    # Selector kategori
    category_selector = 'button.DkEaL'
    closed_selector = 'span.fCEvvc'  # Selector untuk "Tutup sementara"

    # Ambil tombol kategori
    category_button = page.query_selector(category_selector)
    closed_status = page.query_selector(closed_selector)

    # Jika tempat tutup sementara, hentikan scraping
    if closed_status and "Tutup sementara" in closed_status.inner_text():
        print("Tempat ini tutup sementara. Skipping...")
        return False

    # Jika tempat termasuk dalam kategori "Layanan Transportasi" atau "Biro Perjalanan dan Wisata", lanjutkan scraping
    if category_button:
        category_text = category_button.inner_text().strip()
        if category_text in ["Layanan Transportasi", "Biro Perjalanan dan Wisata", "Biro wisata", "Depot bus", "Agen Tiket Bus"]:
            print(f"Kategori sesuai: {category_text}. Scraping data...")
            return True

    # Jika kategori tidak sesuai, skip tempat tersebut
    print("Kategori tidak sesuai. Skipping...")
    return False

def reload_until_success(page, url, max_attempts=5, timeout=10000):
    """
    Mencoba memuat halaman hingga networkidle atau mencapai batas maksimum percobaan.
    """
    attempt = 0
    while attempt < max_attempts:
        try:
            print(f"Attempt {attempt+1}/{max_attempts} to load: {url}")
            page.goto(url)
            page.wait_for_load_state("networkidle", timeout=timeout)
            print("Page loaded successfully!")
            return True  # Berhasil, keluar dari fungsi
        except Exception as e:
            attempt += 1
            print(f"Failed to load page ({attempt}/{max_attempts}): {e}")
            if attempt < max_attempts:
                print("Retrying...")
                page.reload()
            else:
                print("Max retries reached. Skipping this link.")
                return False

def scrape_gmaps():
    try:
        search_query = config.SEARCH_QUERY
        url = f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            if os.path.isfile(COOKIE_FILE):
                load_cookies(context)
            page = context.new_page()
            page.set_default_navigation_timeout(60000)
            page.goto(url)
            slow_scroll(page)
            html_content = page.content()
            place_links = get_all_links(html_content)

            #banyak travel
            print('total travel: ', len(place_links))
            total_travel = len(place_links)
            i = 0
            places = []
            #cari data di link
            for link in place_links:
                print(f"Checking link: {link}")
                if not reload_until_success(page, link):
                    continue
    
                i+=1
                print(f"Data checked: {round((i/total_travel)*100, 2)}% ({i}/{total_travel})")

                if filter(page):
                    print("Kategori sesuai, scraping data...")
                    place_details = get_place_details(page, link)

                #travel relevan apa tidak
                    if is_relevant_travel(place_details["name"], place_details["address"]):
                        place_details["is_relevant"] = True
                        places.append(place_details)
                        df = pd.DataFrame(places)
                        save_path = r"F:\Isg\tpbw\gmaps_scrap\data"  # Tentukan folder penyimpanan
                        os.makedirs(save_path, exist_ok=True)  # Buat folder jika belum ada
                        csv_file = os.path.join(save_path, f"{search_query}.csv")  
                        df.to_csv(csv_file, index=False)
                        print("Google Maps data saved to CSV!")
                    else:
                        print(f"Skipping irrelevant travel: {place_details['name']}")
                else:
                    print("Category not satisfied, skipping...")

            print("All Google Maps data saved to CSV!")
            browser.close()
    except Exception as e:
        if "context or browser has been closed" in str(e):
            print("Program terminated by the user")
            sys.exit()

def get_place_details(page, url):
    """Extract details from a single place page."""
    html_content = page.content()
    soup = BeautifulSoup(html_content, 'html.parser')

    try:
        name = soup.find('h1').text.strip()
    except AttributeError:
        name = ""

    try:
        rating = soup.find('span', class_='MW4etd').text.strip()
    except AttributeError:
        rating = ""

    try:
        address = soup.find('div', class_='Io6YTe fontBodyMedium kR99db fdkmkc').text.strip()
    except AttributeError:
        address = ""

    try:
        ul_element = soup.find("ul", class_="fontTitleSmall")
        li_element = ul_element.find("li", class_="G8aQO").text
        hours_today = li_element
    except AttributeError:
        hours_today = ""

    try:
        website = soup.find("a", {"data-item-id": "authority"}).get('href').strip()
    except AttributeError:
        website = ""

    try:
        phone_element = soup.find("button", {"data-item-id": lambda x: x and x.startswith("phone:")})
        phone =  phone_element.find("div", class_="Io6YTe fontBodyMedium kR99db fdkmkc").text.strip()
    except AttributeError:
        phone = ""

    latitude, longitude = get_coordinates(url)

    return {
        "name": name,
        "rating": rating,
        "address": address,
        "hours_today": hours_today,
        "website": website,
        "phone": phone,
        "latitude": latitude,
        "longitude": longitude,
        "link": url
    }