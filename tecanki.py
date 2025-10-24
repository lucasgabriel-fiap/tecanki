#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
══════════════════════════════════════════════════════════════
                  TECANKI - TEC CONCURSOS                     
              Automação de Cards para Anki                            
          VERSÃO COM COMENTÁRIOS DO FÓRUM 
══════════════════════════════════════════════════════════════
"""

import time
import re
import sys
import requests
from typing import Optional, Tuple, Dict
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup, NavigableString, Tag, Comment
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich.prompt import Prompt, IntPrompt
from rich.table import Table
from rich import box

# ═══════════════════════════════════════════════════════════════
# CONFIGURAÇÕES
# ═══════════════════════════════════════════════════════════════

ANKI_ENDPOINT = "http://127.0.0.1:8765"
ANKI_TIMEOUT = 120
ANKI_VERSION = 6

TIMEOUT_ELEMENTO = 10
DELAY_COMENTARIO = 2.0
DELAY_NAVEGACAO = 2.5
DELAY_RESPOSTA = 1.0
DELAY_FORUM = 3.0

TIPO_NOTA = "Basic"
CAMPO_FRENTE = "Front"
CAMPO_VERSO = "Back"

COMENTARIO_INDISPONIVEL = "⚠️ Comentário não disponível para esta questão."
FORUM_INDISPONIVEL = "⚠️ Fórum não disponível para esta questão."

MAX_IMG_URL_CHARS = 300
DROP_DATA_URI_IMAGES = True

console = Console()

# ═══════════════════════════════════════════════════════════════
# PROCESSAMENTO HTML
# ═══════════════════════════════════════════════════════════════

def is_tag(o): 
    return isinstance(o, Tag)

def is_str(o): 
    return isinstance(o, NavigableString)

def text_with_br(node: Tag) -> str:
    """Extrai texto preservando <br> como quebras de linha"""
    parts = []
    for ch in node.children:
        if is_str(ch):
            parts.append(str(ch))
        elif is_tag(ch):
            if ch.name and ch.name.lower() == "br":
                parts.append("\n")
            else:
                parts.append(text_with_br(ch))
    out = "".join(parts).replace("\u00A0", " ")
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip("\n")

def normalize_mathjax(soup: BeautifulSoup):
    """Converte MathJax para LaTeX"""
    for sc in list(soup.select('script[type^="math/tex"]')):
        tex = (sc.string or sc.get_text() or "").strip()
        if not tex:
            sc.decompose()
            continue
        is_display = "mode=display" in (sc.get("type") or "")
        wrapped = f"\\[{tex}\\]" if is_display else f"\\({tex}\\)"
        repl = soup.new_string(wrapped)

        target = sc
        for _ in range(8):
            if not isinstance(target, Tag) or target.parent is None:
                break
            classes = target.get("class", [])
            if "render-latex" in classes or "MathJax" in classes:
                break
            target = target.parent
        if isinstance(target, Tag) and (
            "render-latex" in (target.get("class") or []) or "MathJax" in (target.get("class") or [])
        ):
            target.replace_with(repl)
        else:
            sc.replace_with(repl)

    for selector in [
        "span.MathJax_Preview",
        "span.MathJax",
        ".MJX_Assistive_MathML",
        "nobr",
        "math",
        "[data-mathml]",
    ]:
        for el in list(soup.select(selector)):
            el.decompose()

    for s in list(soup.find_all("script")):
        s.decompose()

SAFE_STYLE_PROPS = {
    "text-align","text-decoration","vertical-align","white-space","display",
    "margin","margin-left","margin-right","margin-top","margin-bottom",
    "padding","padding-left","padding-right","padding-top","padding-bottom",
    "font-weight","font-style","font-size","line-height",
    "color","background-color",
    "border","border-top","border-right","border-bottom","border-left",
    "border-collapse","border-spacing",
    "width","height","max-width","max-height","cursor",
}
BLOCKED_PROPS = {"filter","opacity","mix-blend-mode","background-image"}

def parse_style_to_dict(style_val: str) -> dict:
    """Converte string de estilo CSS em dicionário"""
    d = {}
    if not style_val: return d
    for chunk in style_val.split(";"):
        if ":" not in chunk: continue
        prop, val = chunk.split(":", 1)
        prop = prop.strip().lower()
        val = val.strip()
        if not prop: continue
        d[prop] = val
    return d

def style_dict_to_str(d: dict) -> str:
    """Converte dicionário de estilos em string CSS"""
    parts = [f"{k}: {v}" for k, v in d.items() if v]
    return "; ".join(parts)

def filter_inline_style(style_value: str) -> str:
    """Filtra propriedades CSS"""
    d = parse_style_to_dict(style_value)
    for bad in list(d.keys()):
        if bad in BLOCKED_PROPS:
            d.pop(bad, None)
    d = {k: v for k, v in d.items() if k in SAFE_STYLE_PROPS}
    return style_dict_to_str(d)

def convert_texto_monospace_to_pre(soup: BeautifulSoup):
    """Converte spans monospace em tags <pre>"""
    for el in list(soup.select("span.texto-monospace")):
        pre = soup.new_tag("pre")
        pre["style"] = "font-family: monospace; white-space: pre; margin: 8px 0;"
        pre.string = text_with_br(el)
        el.replace_with(pre)
    for br in list(soup.select("pre + br")):
        br.decompose()

TABLE_ALLOWED_ATTRS = {
    "style","align","valign","id","width","height",
    "border","cellpadding","cellspacing","summary",
    "colspan","rowspan",
}

def clean_noise(soup: BeautifulSoup, preserve_classes: bool = False):
    """Remove ruído e elementos desnecessários"""
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)): 
        c.extract()
    for s in list(soup.find_all("script")): 
        s.decompose()
    for sel in ["p.elemento-vazio","div.elemento-vazio"]:
        for el in list(soup.select(sel)): 
            el.decompose()

    for tag in list(soup.find_all(True)):
        if tag.name == "img":
            src = (tag.get("src") or "").strip()
            if (DROP_DATA_URI_IMAGES and src.lower().startswith("data:")) or (len(src) > MAX_IMG_URL_CHARS):
                tag.decompose()
                continue

        if not preserve_classes and "class" in tag.attrs:
            del tag.attrs["class"]

        allowed_attrs = set()
        if tag.name in ("img",):
            allowed_attrs = {"src","alt","style","width","height","id"}
        elif tag.name in ("a",):
            allowed_attrs = {"href","target","rel","style","id"}
        elif tag.name in ("table","tbody","thead","tr","td","th","caption","colgroup","col"):
            allowed_attrs = set(TABLE_ALLOWED_ATTRS)
        elif tag.name in (
            "pre","p","ul","ol","li","blockquote","strong","em","i","b","u","sup","sub",
            "div","span","br","h1","h2","h3","h4","h5","h6","hr"
        ):
            allowed_attrs = {"style","align","id","width","height"}
        else:
            tag.unwrap()
            continue

        if tag.name == "span":
            if not any(a in tag.attrs for a in ("style","align","id")):
                tag.unwrap()
                continue

        if "style" in tag.attrs:
            st = filter_inline_style(tag.get("style",""))
            if st: tag["style"] = st
            else: del tag.attrs["style"]

        for attr in list(tag.attrs.keys()):
            if attr not in allowed_attrs:
                del tag.attrs[attr]

def extract_question_and_choices(soup: BeautifulSoup) -> str:
    """Extrai questão e alternativas de forma estruturada"""
    out_parts = []

    container = soup.select_one("article.questao-enunciado")
    if not container:
        return str(soup.body or soup)

    enunciado = container.select_one("div.questao-enunciado-texto")
    if enunciado:
        enun = BeautifulSoup(str(enunciado), "lxml")
        convert_texto_monospace_to_pre(enun)
        normalize_mathjax(enun)
        clean_noise(enun)
        inner_html = "".join(
            str(ch) for ch in (enun.body or enun).children
            if not (is_str(ch) and str(ch).strip() == "")
        ).strip()
        out_parts.append(inner_html)

    ul = container.select_one("ul.questao-enunciado-alternativas")
    if ul:
        itens = []
        for li in ul.find_all("li", recursive=False):
            letra_span = li.select_one(".questao-enunciado-alternativa-opcao label")
            letra = letra_span.get_text(strip=True) if letra_span else ""

            texto_div = li.select_one(".questao-enunciado-alternativa-texto")
            if texto_div:
                tx = BeautifulSoup(str(texto_div), "lxml")
                convert_texto_monospace_to_pre(tx)
                normalize_mathjax(tx)
                clean_noise(tx)
                texto_inner = "".join(
                    str(ch) for ch in (tx.body or tx).children
                    if not (is_str(ch) and str(ch).strip() == "")
                ).strip()
                itens.append(f"  <li>{letra} {texto_inner}</li>")
        if itens:
            out_parts.append("<ul>\n" + "\n".join(itens) + "\n</ul>")

    return "\n".join(p for p in out_parts if p)

def processar_html(html: str) -> str:
    """Processa HTML"""
    if not html or COMENTARIO_INDISPONIVEL in html:
        return html
    
    if not html.strip():
        return "ERRO: O HTML está vazio."
    
    try:
        soup = BeautifulSoup(html, "lxml")
        convert_texto_monospace_to_pre(soup)
        normalize_mathjax(soup)

        html_final = extract_question_and_choices(soup).strip()
        
        if not html_final:
            comentario = soup.select_one("div.questao-complementos-comentario-conteudo-texto")
            if comentario:
                cm = BeautifulSoup(str(comentario), "lxml")
                convert_texto_monospace_to_pre(cm)
                normalize_mathjax(cm)
                clean_noise(cm)
                html_final = "".join(str(ch) for ch in (cm.body or cm).children)

        if html_final:
            final_soup = BeautifulSoup(html_final, "lxml")
            normalize_mathjax(final_soup)
            clean_noise(final_soup)
            inner = "".join(str(ch) for ch in (final_soup.body or final_soup).children).strip()
            return f'<div style="line-height:1.6; font-size:16px; max-width:100%;">{inner}</div>'
        else:
            return "ERRO: Nenhum container de questão ou comentário foi encontrado."
    except Exception as e:
        return f"Ocorreu um erro inesperado: {e}"

# ═══════════════════════════════════════════════════════════════
# ANKI CLIENT
# ═══════════════════════════════════════════════════════════════

class AnkiClient:
    """Cliente para comunicação com AnkiConnect"""
    
    def chamar_anki(self, action: str, params: dict = None) -> dict:
        """Faz chamada à API do AnkiConnect"""
        payload = {
            "action": action,
            "version": ANKI_VERSION,
            "params": params or {}
        }
        
        resp = requests.post(ANKI_ENDPOINT, json=payload, timeout=ANKI_TIMEOUT)
        resp.raise_for_status()
        
        data = resp.json()
        if len(data) != 2:
            raise Exception(f"Resposta inválida do Anki: {data}")
        if data.get("error"):
            raise Exception(f"Erro no Anki: {data['error']}")
        
        return data.get("result")
    
    def testar_conexao(self) -> bool:
        """Testa conexão com AnkiConnect"""
        try:
            self.chamar_anki("version")
            return True
        except:
            return False
    
    def criar_deck(self, nome: str):
        """Cria deck se não existir"""
        self.chamar_anki("createDeck", {"deck": nome})
    
    def adicionar_nota(self, deck: str, frente: str, verso: str):
        """Adiciona nota ao Anki - PERMITE DUPLICATAS"""
        nota = {
            "deckName": deck,
            "modelName": TIPO_NOTA,
            "fields": {
                CAMPO_FRENTE: frente,
                CAMPO_VERSO: verso
            },
            "options": {
                "allowDuplicate": True,
                "duplicateScope": "deck"
            },
            "tags": ["tec-bot"]
        }
        
        self.chamar_anki("addNote", {"note": nota})

# ═══════════════════════════════════════════════════════════════
# GERENCIADOR DE COMENTÁRIOS DO FÓRUM
# ═══════════════════════════════════════════════════════════════

class ForumManager:
    """Gerencia extração e formatação de comentários do fórum TEC"""
    
    SELECTORS = {
        "container": "ul.discussao-comentarios",
        "comentario_item": "li",
        "comentario_visivel": ".discussao-comentario-corpo",
        "votos": ".discussao-comentario-nota-numero span",
        "usuario_foto": ".post-cabecalho-perfil a img",
        "usuario_nome": ".link-professor",
        "usuario_pontos": ".votos .pontos",
        "comentario_data": ".post-cabecalho-perfil-data",
        "comentario_texto": ".discussao-comentario-post-texto",
    }
    
    def __init__(self, driver):
        self.driver = driver
    
    def abrir_forum(self) -> bool:
        """Pressiona F para abrir comentários do fórum - COM TRATAMENTO DE ERRO"""
        try:
            console.print("[cyan]⏳ Tentando abrir fórum...[/cyan]")
            body = self.driver.find_element(By.TAG_NAME, "body")
            body.send_keys("f")
            time.sleep(DELAY_FORUM)
            
            # ✅ Tenta verificar se o fórum carregou (COM TIMEOUT)
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, self.SELECTORS["container"]))
                )
                time.sleep(2.0)  # Aguarda AngularJS
                console.print("[green]✅ Fórum carregado[/green]")
                return True
            except TimeoutException:
                console.print("[yellow]⚠️  Fórum não disponível (timeout)[/yellow]")
                return False
                
        except Exception as e:
            console.print(f"[yellow]⚠️  Erro ao abrir fórum: {e}[/yellow]")
            return False
    
    def extrair_comentarios(self) -> list:
        """Extrai todos os comentários visíveis do fórum - COM TRATAMENTO DE ERRO"""
        comentarios = []
        
        try:
            # ✅ Verifica se container existe
            try:
                container = self.driver.find_element(By.CSS_SELECTOR, self.SELECTORS["container"])
            except NoSuchElementException:
                console.print("[yellow]⚠️  Container de comentários não encontrado[/yellow]")
                return []
            
            itens = container.find_elements(By.CSS_SELECTOR, self.SELECTORS["comentario_item"])
            
            if not itens:
                console.print("[yellow]⚠️  Nenhum comentário encontrado no fórum[/yellow]")
                return []
            
            console.print(f"[cyan]📊 Processando {len(itens)} elementos...[/cyan]")
            
            for idx, item in enumerate(itens, 1):
                try:
                    # Verifica se comentário está visível
                    try:
                        item.find_element(By.CSS_SELECTOR, self.SELECTORS["comentario_visivel"])
                    except:
                        continue
                    
                    comentario = self._extrair_dados_comentario(item)
                    
                    if comentario and comentario.get('texto_html'):
                        comentarios.append(comentario)
                        console.print(f"[green]  ✓ Comentário {len(comentarios)} extraído[/green]")
                
                except Exception:
                    continue
            
            if comentarios:
                console.print(f"[green]✅ {len(comentarios)} comentários extraídos[/green]")
            else:
                console.print("[yellow]⚠️  Nenhum comentário válido extraído[/yellow]")
            
            return comentarios
        
        except Exception as e:
            console.print(f"[yellow]⚠️  Erro ao extrair comentários: {e}[/yellow]")
            return []
    
    def _extrair_dados_comentario(self, elemento) -> dict:
        """Extrai dados de um comentário individual"""
        try:
            # Votos
            try:
                votos_elem = elemento.find_element(By.CSS_SELECTOR, self.SELECTORS["votos"])
                votos = votos_elem.text.strip()
            except:
                votos = "0"
            
            # Usuário - Nome
            try:
                nome_elem = elemento.find_element(By.CSS_SELECTOR, self.SELECTORS["usuario_nome"])
                usuario_nome = nome_elem.text.strip()
            except:
                usuario_nome = "Usuário"
            
            # Usuário - Foto
            try:
                foto_elem = elemento.find_element(By.CSS_SELECTOR, self.SELECTORS["usuario_foto"])
                usuario_foto = foto_elem.get_attribute("src") or ""
                if not usuario_foto or "avatar.png" in usuario_foto:
                    usuario_foto = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Crect fill='%23ddd' width='40' height='40'/%3E%3Ctext x='20' y='25' text-anchor='middle' fill='%23666' font-size='20'%3E👤%3C/text%3E%3C/svg%3E"
            except:
                usuario_foto = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Crect fill='%23ddd' width='40' height='40'/%3E%3Ctext x='20' y='25' text-anchor='middle' fill='%23666' font-size='20'%3E👤%3C/text%3E%3C/svg%3E"
            
            # Pontos do usuário
            try:
                pontos_elem = elemento.find_element(By.CSS_SELECTOR, self.SELECTORS["usuario_pontos"])
                usuario_pontos = pontos_elem.text.strip()
            except:
                usuario_pontos = "0 pontos"
            
            # Data
            try:
                data_elem = elemento.find_element(By.CSS_SELECTOR, self.SELECTORS["comentario_data"])
                data = data_elem.text.strip()
            except:
                data = ""
            
            # Texto do comentário
            try:
                texto_elem = elemento.find_element(By.CSS_SELECTOR, self.SELECTORS["comentario_texto"])
                texto_html = texto_elem.get_attribute("innerHTML") or ""
                
                if texto_html:
                    soup = BeautifulSoup(texto_html, "lxml")
                    for tag in soup.find_all(['script', 'style']):
                        tag.decompose()
                    texto_html = str(soup)
            except:
                texto_html = ""
            
            if not texto_html or not texto_html.strip():
                return None
            
            return {
                "votos": votos,
                "usuario": {
                    "nome": usuario_nome,
                    "foto": usuario_foto,
                    "pontos": usuario_pontos
                },
                "data": data,
                "texto_html": texto_html
            }
        
        except Exception:
            return None
    
    def formatar_para_anki(self, comentarios: list) -> str:
        """Formata comentários do fórum para HTML do Anki"""
        if not comentarios:
            return '<div style="padding: 20px; text-align: center; color: #999; font-style: italic;">🔭 Nenhum comentário disponível no fórum</div>'
        
        html_parts = ['<div class="forum-comentarios" style="font-family: Arial, sans-serif; margin-top: 20px;">']
        html_parts.append('<h2 style="color: #2196F3; border-bottom: 3px solid #2196F3; padding-bottom: 8px; margin-bottom: 20px;">💬 Comentários do Fórum ({} comentários)</h2>'.format(len(comentarios)))
        
        comentarios_ordenados = sorted(
            comentarios, 
            key=lambda x: self._extrair_numero_votos(x['votos']), 
            reverse=True
        )
        
        for idx, c in enumerate(comentarios_ordenados, 1):
            votos_num = self._extrair_numero_votos(c['votos'])
            if votos_num > 100:
                cor_voto = '#4CAF50'
            elif votos_num > 20:
                cor_voto = '#2196F3'
            elif votos_num >= 0:
                cor_voto = '#757575'
            else:
                cor_voto = '#F44336'
            
            texto_processado = self._processar_texto_comentario(c['texto_html'])
            
            html_parts.append(f'''
            <div class="comentario" style="
                border-left: 4px solid {cor_voto}; 
                padding: 15px; 
                margin: 15px 0; 
                background: #fafafa;
                border-radius: 6px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            ">
                <div style="display: flex; align-items: center; margin-bottom: 12px;">
                    <img src="{c['usuario']['foto']}" 
                         style="width: 40px; height: 40px; border-radius: 50%; margin-right: 12px; border: 2px solid #ddd;"
                         onerror="this.src='data:image/svg+xml,%3Csvg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'40\\' height=\\'40\\'%3E%3Crect fill=\\'%23ddd\\' width=\\'40\\' height=\\'40\\'/%3E%3Ctext x=\\'20\\' y=\\'25\\' text-anchor=\\'middle\\' fill=\\'%23666\\' font-size=\\'20\\'%3E👤%3C/text%3E%3C/svg%3E'">
                    <div style="flex: 1;">
                        <div>
                            <strong style="color: #333; font-size: 15px;">{c['usuario']['nome']}</strong>
                            <span style="color: #999; font-size: 12px; margin-left: 8px;">• {c['data']}</span>
                        </div>
                        <div style="color: #666; font-size: 12px;">{c['usuario']['pontos']}</div>
                    </div>
                    <div style="
                        background: {cor_voto}; 
                        color: white; 
                        padding: 6px 14px; 
                        border-radius: 20px;
                        font-weight: bold;
                        font-size: 13px;
                        min-width: 50px;
                        text-align: center;
                    ">
                        ⬆ {c['votos']}
                    </div>
                </div>
                <div style="
                    line-height: 1.7; 
                    color: #333;
                    font-size: 15px;
                    word-wrap: break-word;
                ">
                    {texto_processado}
                </div>
            </div>
            ''')
        
        html_parts.append('</div>')
        return ''.join(html_parts)
    
    def _processar_texto_comentario(self, html: str) -> str:
        """Processa o HTML do texto do comentário"""
        try:
            soup = BeautifulSoup(html, "lxml")
            
            for img in soup.find_all('img'):
                src = img.get('src', '')
                if src.startswith('data:') and len(src) > MAX_IMG_URL_CHARS:
                    img.decompose()
                    continue
                
                style = img.get('style', '')
                img['style'] = f"{style}; max-width: 100%; height: auto; display: block; margin: 10px 0; border-radius: 4px;"
            
            for script in soup.find_all('script'):
                script.decompose()
            
            body = soup.body if soup.body else soup
            conteudo = ''.join(str(child) for child in body.children)
            
            return conteudo.strip()
        except:
            return html
    
    def _extrair_numero_votos(self, texto_votos: str) -> int:
        """Extrai número inteiro dos votos"""
        try:
            numero = ''.join(c for c in texto_votos if c.isdigit() or c == '-')
            return int(numero) if numero else 0
        except:
            return 0
    
    def fechar_forum(self):
        """Fecha o fórum (pressiona ESC) - COM TRATAMENTO DE ERRO"""
        try:
            body = self.driver.find_element(By.TAG_NAME, "body")
            body.send_keys(Keys.ESCAPE)
            time.sleep(1.0)
        except Exception:
            pass  # Ignora erros ao fechar

# ═══════════════════════════════════════════════════════════════
# NAVEGADOR TEC
# ═══════════════════════════════════════════════════════════════

class NavegadorTEC:
    """Controla navegação no site TEC Concursos"""
    
    def __init__(self):
        self.driver = None
        self.forum_manager = None
    
    def iniciar(self):
        """Inicia navegador"""
        console.print("[cyan]🌐 Iniciando navegador...[/cyan]")
        
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        
        try:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(999999)
            self.driver.set_script_timeout(999999)
            console.print("[green]✅ Chrome iniciado[/green]")
            self.forum_manager = ForumManager(self.driver)
        except:
            try:
                service = Service(EdgeChromiumDriverManager().install())
                self.driver = webdriver.Edge(service=service, options=options)
                self.driver.set_page_load_timeout(999999)
                self.driver.set_script_timeout(999999)
                console.print("[green]✅ Edge iniciado[/green]")
                self.forum_manager = ForumManager(self.driver)
            except Exception as e:
                raise Exception(f"Não foi possível iniciar navegador: {e}")
    
    def navegar_tec(self):
        """Navega para o TEC"""
        console.print("[cyan]🔐 Aguardando acesso ao TEC...[/cyan]")
        console.print("[yellow]⚠️  Faça login e vá até uma questão[/yellow]")
        self.driver.get("https://www.tecconcursos.com.br/login")
        input("\n[Pressione ENTER quando estiver numa questão] ")
    
    def validar_questao(self) -> bool:
        """Valida se está numa página de questão"""
        try:
            self.driver.find_element(By.CSS_SELECTOR, "article[ng-if*='questao']")
            return True
        except:
            return False
    
    def capturar_questao(self) -> Optional[str]:
        """Captura HTML da questão"""
        try:
            elemento = self.driver.find_element(By.CSS_SELECTOR, "article[ng-if*='questao']")
            return elemento.get_attribute("outerHTML")
        except:
            return None
    
    def abrir_comentario(self) -> bool:
        """Abre o comentário (tecla O) - COM TRATAMENTO DE ERRO"""
        try:
            console.print("[cyan]⏳ Tentando abrir comentário oficial...[/cyan]")
            self.driver.find_element(By.TAG_NAME, "body").send_keys("o")
            time.sleep(DELAY_COMENTARIO)
            
            # ✅ Verifica se comentário abriu (COM TIMEOUT)
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "article[ng-if*=\"comentario\"]"))
                )
                console.print("[green]✅ Comentário oficial aberto[/green]")
                return True
            except TimeoutException:
                console.print("[yellow]⚠️  Comentário oficial não disponível[/yellow]")
                return False
        except Exception as e:
            console.print(f"[yellow]⚠️  Erro ao abrir comentário: {e}[/yellow]")
            return False
    
    def capturar_comentario(self) -> str:
        """Captura o HTML do comentário - COM TRATAMENTO DE ERRO"""
        try:
            elemento = self.driver.find_element(By.CSS_SELECTOR, "div[tec-formatar-html='vm.comentario.textoComentario']")
            return elemento.get_attribute("outerHTML")
        except:
            return COMENTARIO_INDISPONIVEL
    
    def capturar_comentarios_forum(self) -> str:
        """Captura comentários do fórum"""
        if not self.forum_manager:
            return FORUM_INDISPONIVEL
        
        try:
            # ✅ Tenta abrir fórum
            if not self.forum_manager.abrir_forum():
                return FORUM_INDISPONIVEL
            
            # ✅ Tenta extrair comentários
            comentarios = self.forum_manager.extrair_comentarios()
            
            # ✅ Fecha fórum (sempre, mesmo se houver erro)
            self.forum_manager.fechar_forum()
            
            # ✅ Formata para Anki
            if comentarios:
                return self.forum_manager.formatar_para_anki(comentarios)
            else:
                return FORUM_INDISPONIVEL
        
        except Exception as e:
            console.print(f"[yellow]⚠️  Erro ao capturar fórum: {e}[/yellow]")
            # ✅ Garante que fecha o fórum mesmo em caso de erro
            try:
                self.forum_manager.fechar_forum()
            except:
                pass
            return FORUM_INDISPONIVEL
    
    def responder_questao_c(self):
        """Responde a questão com alternativa C e confirma"""
        try:
            body = self.driver.find_element(By.TAG_NAME, "body")
            body.send_keys("c")
            time.sleep(DELAY_RESPOSTA)
            body.send_keys(Keys.RETURN)
            time.sleep(DELAY_RESPOSTA)
            console.print("[green]✅ Questão respondida (C)[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠️  Não foi possível responder: {e}[/yellow]")
    
    def navegar_proxima(self, modo: str):
        """Navega para próxima questão"""
        try:
            body = self.driver.find_element(By.TAG_NAME, "body")
            
            if modo == "proxima":
                body.send_keys(Keys.ARROW_RIGHT)
            else:
                body.send_keys("l")
            
            time.sleep(DELAY_NAVEGACAO)
            return self.validar_questao()
        except:
            return False
    
    def fechar(self):
        """Fecha o navegador"""
        if self.driver:
            self.driver.quit()

# ═══════════════════════════════════════════════════════════════
# INTERFACE
# ═══════════════════════════════════════════════════════════════

def exibir_titulo():
    """Exibe título do programa"""
    console.print(Panel.fit(
        "[bold cyan]🤖 ANKI BOT - TEC CONCURSOS[/bold cyan]\n"
        "[bold green]VERSÃO COM COMENTÁRIOS DO FÓRUM[/bold green]\n",
        border_style="cyan"
    ))

def solicitar_config() -> Tuple[str, int, str, bool]:
    """Solicita configurações do usuário"""
    console.print("\n[bold yellow]⚙️  CONFIGURAÇÃO[/bold yellow]\n")
    
    deck = Prompt.ask("[cyan]Nome do deck[/cyan]")
    quantidade = IntPrompt.ask("[cyan]Quantas questões processar?[/cyan]", default=10)
    
    console.print("\n[cyan]💬 Incluir comentários do fórum?[/cyan]")
    console.print("  [yellow]Isso capturará todos os comentários dos usuários com imagens e formatação[/yellow]")
    incluir_forum_input = Prompt.ask("[cyan]Incluir fórum?[/cyan]", choices=["s", "n"], default="s")
    incluir_forum = (incluir_forum_input.lower() == "s")
    
    console.print("\n[cyan]Modo de navegação:[/cyan]")
    console.print("  [1] → Próxima sequencial (NÃO responde)")
    console.print("  [2] L Aleatória não resolvida (responde C)")
    
    modo = Prompt.ask("[cyan]Escolha[/cyan]", choices=["1", "2"], default="1")
    modo_nav = "proxima" if modo == "1" else "aleatoria"
    
    return deck, quantidade, modo_nav, incluir_forum

def exibir_relatorio(stats: dict):
    """Exibe relatório final"""
    tabela = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    tabela.add_column("Item", style="cyan bold")
    tabela.add_column("Valor", style="white")
    
    tabela.add_row("📊 Total", str(stats['total']))
    tabela.add_row("✅ Sucesso", f"[green]{stats['sucesso']}[/green]")
    tabela.add_row("⚠️  Sem comentário", f"[yellow]{stats['sem_comentario']}[/yellow]")
    tabela.add_row("⚠️  Sem fórum", f"[yellow]{stats['sem_forum']}[/yellow]")
    tabela.add_row("❌ Erros", f"[red]{stats['erros']}[/red]")
    tabela.add_row("⏱️  Tempo", stats['tempo'])
    tabela.add_row("📦 Deck", stats['deck'])
    if stats.get('forum'):
        tabela.add_row("💬 Fórum", "[green]✅ Ativado[/green]")
    
    console.print(Panel(tabela, title="[bold green]✅ CONCLUÍDO![/bold green]", border_style="green"))

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    """Função principal"""
    inicio = time.time()
    
    exibir_titulo()
    
    deck, quantidade, modo, incluir_forum = solicitar_config()
    
    console.print("\n[cyan]🔍 Validando pré-requisitos...[/cyan]")
    anki = AnkiClient()
    
    if not anki.testar_conexao():
        console.print("[red]❌ Anki não está rodando ou AnkiConnect não instalado[/red]")
        console.print("[yellow]Instale: https://ankiweb.net/shared/info/2055492159[/yellow]")
        return
    
    console.print("[green]✅ AnkiConnect OK[/green]")
    
    try:
        anki.criar_deck(deck)
        console.print(f"[green]✅ Deck '{deck}' pronto[/green]")
    except Exception as e:
        console.print(f"[red]❌ Erro ao criar deck: {e}[/red]")
        return
    
    nav = NavegadorTEC()
    try:
        nav.iniciar()
        nav.navegar_tec()
        
        if not nav.validar_questao():
            console.print("[red]❌ Não está numa página de questão[/red]")
            return
        
        console.print("[green]✅ Pronto para começar![/green]\n")
    
    except Exception as e:
        console.print(f"[red]❌ Erro no navegador: {e}[/red]")
        return
    
    stats = {
        "total": quantidade, 
        "sucesso": 0, 
        "sem_comentario": 0,
        "sem_forum": 0,
        "erros": 0, 
        "deck": deck,
        "forum": incluir_forum
    }
    
    console.print("[bold green]🚀 PROCESSANDO QUESTÕES[/bold green]\n")
    
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), 
                  BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                  console=console) as progress:
        
        task = progress.add_task(f"[cyan]Processando...", total=quantidade)
        
        for i in range(1, quantidade + 1):
            console.print(f"\n[bold cyan]━━━ Questão {i}/{quantidade} ━━━[/bold cyan]")
            
            try:
                # 1. CAPTURA QUESTÃO
                console.print("[cyan]⏳ Capturando questão...[/cyan]")
                html_questao = nav.capturar_questao()
                if not html_questao:
                    raise Exception("Falha ao capturar questão")
                console.print("[green]✅ Questão capturada[/green]")
                
                # 2. CAPTURA COMENTÁRIO OFICIAL
                comentario_abriu = nav.abrir_comentario()
                html_comentario = nav.capturar_comentario()
                
                if COMENTARIO_INDISPONIVEL in html_comentario or not comentario_abriu:
                    stats["sem_comentario"] += 1
                    html_comentario = COMENTARIO_INDISPONIVEL
                else:
                    console.print("[green]✅ Comentário oficial capturado[/green]")
                
                # 3. CAPTURA COMENTÁRIOS DO FÓRUM
                html_forum = ""
                if incluir_forum:
                    html_forum = nav.capturar_comentarios_forum()
                    
                    if FORUM_INDISPONIVEL in html_forum or '🔭' in html_forum:
                        stats["sem_forum"] += 1
                    else:
                        console.print("[green]✅ Fórum capturado[/green]")
                
                # 4. PROCESSA HTML
                console.print("[cyan]⏳ Processando HTML...[/cyan]")
                questao_limpa = processar_html(html_questao)
                comentario_limpo = processar_html(html_comentario) if COMENTARIO_INDISPONIVEL not in html_comentario else COMENTARIO_INDISPONIVEL
                
                # 5. MONTA VERSO COMBINADO
                if incluir_forum and html_forum and FORUM_INDISPONIVEL not in html_forum and '🔭' not in html_forum:
                    separador = '''
                    <div style="margin: 30px 0; text-align: center;">
                        <hr style="border: none; border-top: 3px solid #2196F3; width: 80%; margin: 20px auto;">
                    </div>
                    '''
                    verso_final = f"{comentario_limpo}{separador}{html_forum}"
                else:
                    verso_final = comentario_limpo
                
                console.print("[green]✅ HTML processado[/green]")
                
                # 6. ENVIA PARA ANKI
                console.print("[cyan]⏳ Enviando para Anki...[/cyan]")
                anki.adicionar_nota(deck, questao_limpa, verso_final)
                console.print(f"[green]✅ Card criado no deck '{deck}'[/green]")
                
                stats["sucesso"] += 1
                
                # 7. RESPONDE (se modo aleatória)
                if modo == "aleatoria":
                    console.print("[cyan]⏳ Respondendo questão (C)...[/cyan]")
                    nav.responder_questao_c()
                
                # 8. NAVEGA (exceto última questão)
                if i < quantidade:
                    console.print("[cyan]⏳ Navegando para próxima...[/cyan]")
                    if not nav.navegar_proxima(modo):
                        raise Exception("Falha ao navegar")
                    console.print("[green]✅ Próxima questão[/green]")
            
            except Exception as e:
                stats["erros"] += 1
                console.print(f"[red]❌ Erro: {e}[/red]")
            
            progress.update(task, advance=1)
    
    tempo_total = time.time() - inicio
    stats["tempo"] = f"{int(tempo_total//60)}min {int(tempo_total%60)}s"
    
    console.print("\n")
    exibir_relatorio(stats)
    
    try:
        nav.fechar()
    except:
        pass

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Interrompido pelo usuário[/yellow]")
    except Exception as e:
        console.print(f"\n[red]❌ Erro fatal: {e}[/red]")
