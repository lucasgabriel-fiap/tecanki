#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
════════════════════════════════════════════════════════════════
                  TECANKI - TEC CONCURSOS                     
              Automação de Cards para Anki                            
════════════════════════════════════════════════════════════════
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

TIPO_NOTA = "Basic"
CAMPO_FRENTE = "Front"
CAMPO_VERSO = "Back"

COMENTARIO_INDISPONIVEL = "⚠️ Comentário não disponível para esta questão."

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
        # Remoção de imagens com link gigante ou data URI
        if tag.name == "img":
            src = (tag.get("src") or "").strip()
            if (DROP_DATA_URI_IMAGES and src.lower().startswith("data:")) or (len(src) > MAX_IMG_URL_CHARS):
                tag.decompose()
                continue

        if not preserve_classes and "class" in tag.attrs:
            del tag.attrs["class"]

        # atributos permitidos por tag
        allowed_attrs = set()
        if tag.name in ("img",):
            allowed_attrs = {"src","alt","style","width","height","id"}
        elif tag.name in ("a",):
            allowed_attrs = {"href","target","rel","style","id"}
        elif tag.name in ("table","tbody","thead","tr","td","th","caption","colgroup","col"):
            allowed_attrs = set(TABLE_ALLOWED_ATTRS)
        elif tag.name in (
            "pre","p","ul","ol","li","blockquote","strong","em","i","b","u","sup","sub",
            "div","span","br"
        ):
            allowed_attrs = {"style","id"}
        else:
            allowed_attrs = {"style","id"}

        to_remove = [k for k in tag.attrs if k not in allowed_attrs]
        for k in to_remove:
            del tag.attrs[k]

        if "style" in tag.attrs:
            tag.attrs["style"] = filter_inline_style(tag.attrs["style"])

def extract_questao_alternativas(soup: BeautifulSoup) -> str:
    """Extrai questão e alternativas de forma estruturada"""
    output_parts = []
    for el in soup.find_all(True, recursive=False):
        if el.name in ("style",):
            continue
        if el.name == "hr" or (el.name in ("div","p") and "separador" in el.get("class",[])):
            output_parts.append("<hr>")
        else:
            html_str = str(el)
            if html_str.strip():
                output_parts.append(html_str)
    return "\n".join(output_parts)

def wrap_em_div_estilizada(html_interno: str) -> str:
    """Envolve conteúdo em div com estilos"""
    return (
        '<div style="font-family: Arial, sans-serif; font-size: 14px; '
        'line-height: 1.6; color: #333; padding: 12px; '
        'background: #fafafa; border-radius: 4px;">\n'
        f'{html_interno}\n'
        '</div>'
    )

def merge_consecutive_breaks(soup: BeautifulSoup):
    """Remove quebras de linha consecutivas excessivas"""
    for br in list(soup.find_all("br")):
        nxt = br.next_sibling
        if nxt and is_tag(nxt) and nxt.name == "br":
            br.decompose()

def processar_html(html_bruto: str) -> str:
    """Processa HTML aplicando limpezas e formatações"""
    if not html_bruto or html_bruto.strip() == "" or COMENTARIO_INDISPONIVEL in html_bruto:
        return html_bruto

    try:
        soup = BeautifulSoup(html_bruto, "lxml")
    except Exception as e:
        console.print(f"[red]Erro ao parsear HTML: {e}[/red]")
        return html_bruto

    normalize_mathjax(soup)
    convert_texto_monospace_to_pre(soup)
    clean_noise(soup, preserve_classes=False)

    body = soup.body if soup.body else soup
    if body.name == "body":
        inner = extract_questao_alternativas(body)
    else:
        inner = str(body)

    merge_consecutive_breaks(soup)

    # Adiciona wrapper com estilos
    final_html = wrap_em_div_estilizada(inner)
    
    return final_html

# ═══════════════════════════════════════════════════════════════
# CLIENTE ANKI
# ═══════════════════════════════════════════════════════════════

class AnkiClient:
    """Cliente para comunicação com AnkiConnect"""
    
    def __init__(self):
        self.endpoint = ANKI_ENDPOINT
        self.timeout = ANKI_TIMEOUT
    
    def invocar(self, acao: str, params: Optional[Dict] = None) -> Dict:
        """Invoca uma ação no AnkiConnect"""
        payload = {
            "action": acao,
            "version": ANKI_VERSION,
            "params": params or {}
        }
        
        try:
            resposta = requests.post(self.endpoint, json=payload, timeout=self.timeout)
            resposta.raise_for_status()
            dados = resposta.json()
            
            if dados.get("error"):
                raise Exception(dados["error"])
            
            return dados.get("result")
        
        except requests.exceptions.RequestException as e:
            raise Exception(f"Erro de conexão: {e}")
    
    def testar_conexao(self) -> bool:
        """Testa conexão com AnkiConnect"""
        try:
            self.invocar("version")
            return True
        except:
            return False
    
    def criar_deck(self, nome: str):
        """Cria um deck no Anki"""
        self.invocar("createDeck", {"deck": nome})
    
    def adicionar_nota(self, deck: str, frente: str, verso: str):
        """Adiciona uma nota ao Anki"""
        nota = {
            "deckName": deck,
            "modelName": TIPO_NOTA,
            "fields": {
                CAMPO_FRENTE: frente,
                CAMPO_VERSO: verso
            },
            "options": {
                "allowDuplicate": True
            }
        }
        self.invocar("addNote", {"note": nota})

# ═══════════════════════════════════════════════════════════════
# NAVEGADOR TEC
# ═══════════════════════════════════════════════════════════════

class NavegadorTEC:
    """Controla o navegador para automação do TEC Concursos"""
    
    def __init__(self):
        self.driver = None
    
    def iniciar(self):
        """Inicia o navegador Chrome/Edge"""
        try:
            opcoes = webdriver.ChromeOptions()
            opcoes.add_argument("--start-maximized")
            opcoes.add_experimental_option("excludeSwitches", ["enable-logging"])
            
            try:
                servico = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=servico, options=opcoes)
            except:
                servico = Service(EdgeChromiumDriverManager().install())
                self.driver = webdriver.Edge(service=servico, options=opcoes)
            
            console.print("[green]✅ Navegador iniciado[/green]")
        
        except Exception as e:
            raise Exception(f"Erro ao iniciar navegador: {e}")
    
    def navegar_tec(self):
        """Navega para o TEC Concursos"""
        console.print("[cyan]🌐 Abrindo TEC Concursos...[/cyan]")
        self.driver.get("https://www.tecconcursos.com.br/questoes")
        console.print("[yellow]⏸️  Faça login e navegue até uma questão[/yellow]")
        console.print("[yellow]⏸️  Pressione ENTER quando estiver pronto...[/yellow]")
        input()
    
    def validar_questao(self) -> bool:
        """Valida se está em uma página de questão"""
        try:
            wait = WebDriverWait(self.driver, TIMEOUT_ELEMENTO)
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div[tec-formatar-html='vm.questao.textoQuestao']")
            ))
            return True
        except:
            return False
    
    def capturar_questao(self) -> str:
        """Captura o HTML da questão"""
        try:
            elemento = self.driver.find_element(
                By.CSS_SELECTOR, 
                "div[tec-formatar-html='vm.questao.textoQuestao']"
            )
            return elemento.get_attribute("outerHTML")
        except Exception as e:
            raise Exception(f"Erro ao capturar questão: {e}")
    
    def abrir_comentario(self) -> bool:
        """Abre o comentário da questão pressionando 'O'"""
        try:
            body = self.driver.find_element(By.TAG_NAME, "body")
            body.send_keys("o")
            time.sleep(DELAY_COMENTARIO)
            
            self.driver.find_element(By.CSS_SELECTOR, "article[ng-if*=\"comentario\"]")
            return True
        except:
            return False
    
    def capturar_comentario(self) -> str:
        """Captura o HTML do comentário"""
        try:
            elemento = self.driver.find_element(By.CSS_SELECTOR, "div[tec-formatar-html='vm.comentario.textoComentario']")
            return elemento.get_attribute("outerHTML")
        except:
            return COMENTARIO_INDISPONIVEL
    
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
        "[bold cyan]🤖 TECANKI - TEC CONCURSOS[/bold cyan]\n"
        "[dim]Automação para Anki[/dim]",
        border_style="cyan"
    ))

def solicitar_config() -> Tuple[str, int, str]:
    """Solicita configurações do usuário"""
    console.print("\n[bold yellow]⚙️  CONFIGURAÇÃO[/bold yellow]\n")
    
    deck = Prompt.ask("[cyan]Nome do deck[/cyan]")
    quantidade = IntPrompt.ask("[cyan]Quantas questões processar?[/cyan]", default=10)
    
    console.print("\n[cyan]Modo de navegação:[/cyan]")
    console.print("  [1] → Próxima sequencial (NÃO responde)")
    console.print("  [2] L Aleatória não resolvida (responde C)")
    
    modo = Prompt.ask("[cyan]Escolha[/cyan]", choices=["1", "2"], default="1")
    modo_nav = "proxima" if modo == "1" else "aleatoria"
    
    return deck, quantidade, modo_nav

def exibir_relatorio(stats: dict):
    """Exibe relatório final"""
    tabela = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    tabela.add_column("Item", style="cyan bold")
    tabela.add_column("Valor", style="white")
    
    tabela.add_row("📊 Total", str(stats['total']))
    tabela.add_row("✅ Sucesso", f"[green]{stats['sucesso']}[/green]")
    tabela.add_row("⚠️  Sem comentário", f"[yellow]{stats['sem_comentario']}[/yellow]")
    tabela.add_row("❌ Erros", f"[red]{stats['erros']}[/red]")
    tabela.add_row("⏱️  Tempo", stats['tempo'])
    tabela.add_row("📦 Deck", stats['deck'])
    
    console.print(Panel(tabela, title="[bold green]✅ CONCLUÍDO![/bold green]", border_style="green"))

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    """Função principal"""
    inicio = time.time()
    
    exibir_titulo()
    
    deck, quantidade, modo = solicitar_config()
    
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
    
    stats = {"total": quantidade, "sucesso": 0, "sem_comentario": 0, "erros": 0, "deck": deck}
    
    console.print("[bold green]🚀 PROCESSANDO QUESTÕES[/bold green]\n")
    
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), 
                  BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                  console=console) as progress:
        
        task = progress.add_task(f"[cyan]Processando...", total=quantidade)
        
        for i in range(1, quantidade + 1):
            console.print(f"\n[bold cyan]━━━ Questão {i}/{quantidade} ━━━[/bold cyan]")
            
            try:
                console.print("[cyan]⏳ Capturando questão...[/cyan]")
                html_questao = nav.capturar_questao()
                if not html_questao:
                    raise Exception("Falha ao capturar questão")
                console.print("[green]✅ Questão capturada[/green]")
                
                console.print("[cyan]⏳ Abrindo comentário (O)...[/cyan]")
                nav.abrir_comentario()
                html_comentario = nav.capturar_comentario()
                
                if COMENTARIO_INDISPONIVEL in html_comentario:
                    stats["sem_comentario"] += 1
                    console.print("[yellow]⚠️  Sem comentário[/yellow]")
                else:
                    console.print("[green]✅ Comentário capturado[/green]")
                
                console.print("[cyan]⏳ Processando HTML...[/cyan]")
                questao_limpa = processar_html(html_questao)
                comentario_limpo = processar_html(html_comentario) if COMENTARIO_INDISPONIVEL not in html_comentario else COMENTARIO_INDISPONIVEL
                console.print("[green]✅ HTML processado[/green]")
                
                console.print("[cyan]⏳ Enviando para Anki...[/cyan]")
                anki.adicionar_nota(deck, questao_limpa, comentario_limpo)
                console.print(f"[green]✅ Card criado no deck '{deck}'[/green]")
                
                stats["sucesso"] += 1
                
                # RESPONDE APENAS se modo for ALEATÓRIA
                if modo == "aleatoria":
                    console.print("[cyan]⏳ Respondendo questão (C)...[/cyan]")
                    nav.responder_questao_c()
                
                # Só navega se NÃO for a última questão
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
    
    console.print("\n[cyan]💡 Navegador permanece aberto. Feche manualmente quando quiser.[/cyan]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️  Interrompido pelo usuário[/yellow]")
    except Exception as e:
        console.print(f"\n[red]❌ Erro fatal: {e}[/red]")
