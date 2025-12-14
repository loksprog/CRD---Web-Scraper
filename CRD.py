from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import json
import csv  # <--- Required for CSV support
import time
import re
import warnings
import requests 
import html 
from bs4 import BeautifulSoup 

# ============================================================
# SILENCE WARNINGS
# ============================================================
warnings.simplefilter(action='ignore', category=FutureWarning)

# ============================================================
# CONFIGURATION
# ============================================================
ARCHIVE_URL = "https://kmt.vander-lingen.nl/archive"
BASE_DOMAIN = "https://kmt.vander-lingen.nl" 
MAX_PAPERS_LIMIT = 1     # Set to 0 to scrape ALL papers

# ============================================================
# SETUP
# ============================================================
def get_driver():
    options = webdriver.ChromeOptions()
    
    # 1. Disable Images (Speed Boost)
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    
    # 2. Eager Loading
    options.page_load_strategy = 'eager'
    
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    return webdriver.Chrome(options=options)

# ============================================================
# HELPER: EXTRACT XML DATA
# ============================================================
def parse_xml_regex(xml_text):
    result = {'reaction_smiles': None, 'molecules': []}
    try:
        # 1. Reaction SMILES
        rxn_match = re.search(r'<reactionSmiles>(.*?)</reactionSmiles>', xml_text, re.DOTALL)
        if rxn_match:
            raw_smiles = rxn_match.group(1).strip()
            result['reaction_smiles'] = html.unescape(raw_smiles)

        # 2. Molecules
        molecule_blocks = re.findall(r'<molecule>(.*?)</molecule>', xml_text, re.DOTALL)
        for block in molecule_blocks:
            def get_tag_val(tag, text):
                match = re.search(f'<{tag}>(.*?)</{tag}>', text, re.DOTALL)
                if match:
                    return html.unescape(match.group(1).strip())
                return None

            result['molecules'].append({
                'role': get_tag_val('role', block),
                'inchiKey': get_tag_val('inchiKey', block),
                'smiles': get_tag_val('smiles', block),
                'name': get_tag_val('name', block),
                'ratio': get_tag_val('ratio', block)
            })
    except Exception as e:
        print(f"      [REGEX ERROR] {e}")
    return result

# ============================================================
# LEVEL 1: SCAN ARCHIVE
# ============================================================
def scan_archive_page(driver):
    reaction_links = []
    try:
        print(f"Loading archive: {ARCHIVE_URL} ...")
        driver.get(ARCHIVE_URL)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        elements = driver.find_elements(By.PARTIAL_LINK_TEXT, "reaction data")
        print(f"Found {len(elements)} total papers available.")
        
        for elem in elements:
            try:
                url = elem.get_attribute("href")
                parent = elem.find_element(By.XPATH, "./..")
                full_text = parent.text
                
                reaction_links.append({
                    "start_url": url,
                    "title_text": full_text
                })
            except:
                continue
    except Exception as e:
        print(f"Error scanning archive: {e}")
        
    return reaction_links


def scrape_single_reaction(driver, link_data, current_index, total_count):
    current_list_url = link_data['start_url']
    
    paper_data = {
        # 'source_title' and 'year' are REMOVED from output
        'doi': None,
        'details_scanned': 0,
        'reactions': [],
        'error': None
    }

    session = requests.Session()
    selenium_cookies = driver.get_cookies()
    for cookie in selenium_cookies:
        session.cookies.set(cookie['name'], cookie['value'])
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    })

    print("="*60)
    page_num = 1
    print(f"\n[{current_index}/{total_count}] STARTING: {link_data['title_text'][:50]}...")

    try:
        while current_list_url:
            # 1. Selenium for List
            driver.get(current_list_url)
            try:
                WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            except:
                break
            
            if not paper_data['doi']:
                doi_match = re.search(r'doi/(.*?)/start', current_list_url)
                if doi_match:
                    paper_data['doi'] = doi_match.group(1)

            # 2. Collect Links
            detail_urls = []
            try:
                detail_elements = driver.find_elements(By.XPATH, "//a[contains(text(), 'Details')]")
                for elem in detail_elements:
                    url = elem.get_attribute('href')
                    if url:
                        detail_urls.append(url)
            except:
                pass

            if not detail_urls:
                break

            # 3. Find Next Page
            next_list_url = None
            try:
                next_btns = driver.find_elements(By.XPATH, "//a[contains(text(), 'Next') or contains(text(), '>')]")
                for btn in next_btns:
                    href = btn.get_attribute('href')
                    if href and "start" in href and href != current_list_url:
                        next_list_url = href
                        break
            except:
                pass

            # 4. Requests for Details
            print("-"*50)
            print(f"   [Page {page_num}] Found {len(detail_urls)} details. Processing...")
            
            for detail_url in detail_urls:
                try:
                    # Download HTML
                    resp = session.get(detail_url, timeout=5)
                    if resp.status_code != 200: continue
                    
                    reaction_entry = { 
                        'details_url': detail_url, 
                        'overall_reaction_smiles': None, 
                        'molecules': []
                    }

                    # Find XML Link
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    xml_link_tag = soup.find('a', string="XML")
                    
                    if xml_link_tag and xml_link_tag.get('href'):
                        xml_href = xml_link_tag.get('href')
                        if xml_href.startswith("/"):
                            xml_href = BASE_DOMAIN + xml_href
                        
                        # Download XML
                        xml_resp = session.get(xml_href, timeout=5)
                        if xml_resp.status_code == 200:
                            parsed = parse_xml_regex(xml_resp.text)
                            reaction_entry['overall_reaction_smiles'] = parsed['reaction_smiles']
                            reaction_entry['molecules'] = parsed['molecules']
                    
                    paper_data['reactions'].append(reaction_entry)
                    paper_data['details_scanned'] += 1
                    
                except Exception:
                    pass

            # 5. Advance
            current_list_url = next_list_url
            page_num += 1
            
            if paper_data['details_scanned'] > 200:
                print("   [Limit] Reached 200 details. Done.")
                break

    except Exception as e:
        paper_data['error'] = str(e)
        
    print(f"   [DONE] Finished {paper_data['doi']} | Scanned: {paper_data['details_scanned']}")
    return paper_data

# ============================================================
# MAIN EXECUTION
# ============================================================
def main():
    print("="*60)
    print("CRD - Web Scraper")
    print("Submitted by: Samantha Singcol & Luke Harvey T, Umpad")
    print("="*60)
    
    driver = get_driver()
    
    try:
        links = scan_archive_page(driver)
        
        if not links:
            print("No links found.")
            return

        if MAX_PAPERS_LIMIT > 0:
            links = links[:MAX_PAPERS_LIMIT]
            print(f"\n--> LIMIT APPLIED: Processing only first {len(links)} papers.")
        
        results = []
        
        for index, link in enumerate(links, start=1):
            data = scrape_single_reaction(driver, link, index, len(links))
            results.append(data)

        # --- 1. SAVE JSON ---
        json_filename = 'kmt_output_sam&luke.json'
        with open(json_filename, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n[SUCCESS] JSON saved to {json_filename}")

        # --- 2. SAVE CSV  ---
        csv_filename = 'kmt_output_sam&luke.csv'
        print(f"Generating Report-Style CSV ({csv_filename})...")
        
        with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            for paper in results:
                doi = paper.get('doi', 'N/A')
                reactions = paper.get('reactions', [])
                
                for rxn in reactions:
                    # -- HEADER INFO (Above the table) --
                    url = rxn.get('details_url', '')
                    overall_smiles = rxn.get('overall_reaction_smiles', '')
                    
                    writer.writerow(["DOI:", doi])
                    writer.writerow(["Details URL:", url])
                    writer.writerow(["Overall Reaction SMILES:", overall_smiles])
                    
                    # -- THE TABLE (Rows of roles, Cols of SMILES) --
                    # Header row for the sub-table
                    writer.writerow(["Role", "Name", "SMILES", "Ratio"])
                    
                    # Data rows
                    molecules = rxn.get('molecules', [])
                    if molecules:
                        for mol in molecules:
                            writer.writerow([
                                mol.get('role', ''),
                                mol.get('name', ''),
                                mol.get('smiles', ''),
                                mol.get('ratio', '')
                            ])
                    else:
                        writer.writerow(["No molecules found in XML", "", "", ""])
                    
                    # Spacer between different URL tables
                    writer.writerow([]) 
                    writer.writerow(["=" * 50])
                    writer.writerow([]) 

        print(f"[SUCCESS] CSV saved to {csv_filename}")

    finally:
        driver.quit()
        print("Browser closed.")

if __name__ == "__main__":
    main()