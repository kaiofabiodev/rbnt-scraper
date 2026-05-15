"""
Scraper do rubinot.com.br via Playwright (roda no GitHub Actions).
Lê watch_list.json do Gist, scrapa cada personagem, salva data.json no Gist.
"""

import asyncio
import json
import os
import sys
import time
import urllib.request
import urllib.error

from playwright.async_api import async_playwright

GIST_ID    = os.environ["GIST_ID"]
GIST_TOKEN = os.environ["GIST_TOKEN"]
BASE_URL   = "https://rubinot.com.br"


# ─── Gist helpers ─────────────────────────────────────────────────────────────

def gist_get_file(filename: str) -> dict | list | None:
    url = f"https://api.github.com/gists/{GIST_ID}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GIST_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "rubinot-scraper",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        raw_url = data["files"][filename]["raw_url"]
        with urllib.request.urlopen(raw_url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[Gist] Erro ao ler {filename}: {e}")
        return None


def gist_patch(files: dict[str, str]):
    payload = json.dumps({"files": {k: {"content": v} for k, v in files.items()}}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}",
        data=payload,
        method="PATCH",
        headers={
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "rubinot-scraper",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        print("[Gist] Dados salvos com sucesso.")
    except Exception as e:
        print(f"[Gist] Erro ao salvar: {e}")


# ─── Scraping ─────────────────────────────────────────────────────────────────

async def scrape_character(page, name: str) -> dict | None:
    url = f"{BASE_URL}/api/characters/search?name={urllib.parse.quote(name)}"
    print(f"[Scraper] Buscando: {name}")
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if resp and resp.status == 200:
            text = await page.inner_text("body")
            data = json.loads(text)

            if "player" not in data:
                print(f"[Scraper] {name}: player não encontrado na resposta")
                return None

            player     = data["player"]
            name_lower = name.lower()

            is_online = False
            for c in data.get("otherCharacters", []):
                if c.get("name", "").lower() == name_lower:
                    is_online = bool(c.get("isOnline", False))
                    break

            guild     = None
            raw_guild = player.get("guild")
            if isinstance(raw_guild, dict):
                guild = raw_guild.get("name") or raw_guild.get("guildName")
            elif isinstance(raw_guild, str):
                guild = raw_guild

            deaths = [
                {
                    "victim": name,
                    "killer": d.get("killedBy", "?"),
                    "level":  d.get("level"),
                    "time":   d.get("time"),
                }
                for d in data.get("deaths", [])
            ]

            result = {
                "name":      player.get("name", name),
                "level":     player.get("level"),
                "vocation":  player.get("vocation"),
                "world":     player.get("world"),
                "sex":       player.get("sex"),
                "residence": player.get("residence"),
                "guild":     guild,
                "is_online": is_online,
                "deaths":    deaths,
                "scraped_at": int(time.time()),
            }
            print(f"[Scraper] {name}: level={result['level']} online={is_online}")
            return result
        else:
            status = resp.status if resp else "?"
            print(f"[Scraper] {name}: HTTP {status}")
            return None
    except Exception as e:
        print(f"[Scraper] {name}: erro — {e}")
        return None


import urllib.parse

async def main():
    watch_list = gist_get_file("watch_list.json")
    if not watch_list:
        print("[Scraper] watch_list.json vazio ou inexistente. Nada a fazer.")
        # Criar arquivo vazio para o Gist ter a estrutura
        gist_patch({"data.json": json.dumps({}, ensure_ascii=False)})
        return

    if isinstance(watch_list, list):
        characters = watch_list
    else:
        characters = watch_list.get("characters", [])

    if not characters:
        print("[Scraper] Nenhum personagem na watch list.")
        gist_patch({"data.json": json.dumps({}, ensure_ascii=False)})
        return

    print(f"[Scraper] {len(characters)} personagens para scraping: {characters}")

    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )

        # Visitar a home primeiro para pegar o cf_clearance
        page = await context.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt', 'en'] });
        """)

        print("[Scraper] Acessando home para resolver Turnstile...")
        try:
            await page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)
            title = await page.title()
            print(f"[Scraper] Título da página: {title}")
        except Exception as e:
            print(f"[Scraper] Aviso ao carregar home: {e}")

        # Scraping de cada personagem
        for char_name in characters:
            result = await scrape_character(page, char_name)
            if result:
                results[char_name.lower()] = result
            await page.wait_for_timeout(2000)  # delay entre requests

        await browser.close()

    print(f"[Scraper] Resultados coletados: {len(results)}/{len(characters)}")
    gist_patch({"data.json": json.dumps(results, ensure_ascii=False, indent=2)})


if __name__ == "__main__":
    asyncio.run(main())
