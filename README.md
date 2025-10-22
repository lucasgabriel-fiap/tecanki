# 🤖 TECANKI

Automação para criar cards no Anki a partir de questões do **TEC Concursos**.

## 📋 Descrição

O TECANKI é um bot que captura questões e comentários do site TEC Concursos e cria automaticamente flashcards no Anki, facilitando seus estudos para concursos públicos.

### ✨ Funcionalidades

- 🎯 Captura automática de questões
- 💬 Extração de comentários dos professores
- 🧹 Limpeza e formatação de HTML
- 🔄 Conversão de LaTeX/MathJax
- 📊 Suporte a tabelas e imagens
- 📦 Criação automática de cards no Anki
- 🎲 Dois modos de navegação: sequencial ou aleatória

## 🚀 Instalação

### Pré-requisitos

1. **Python 3.7+**
2. **Anki Desktop** instalado e rodando
3. **AnkiConnect** addon: [Instalar aqui](https://ankiweb.net/shared/info/2055492159)
4. **Google Chrome** instalado

### Instalando as dependências

```bash
pip install -r requirements.txt
```

## 📖 Como usar

1. **Inicie o Anki** no seu computador

2. **Execute o programa:**
   ```bash
   python tecanki.py
   ```

3. **Configure:**
   - Nome do deck
   - Quantidade de questões a processar
   - Modo de navegação:
     - **Modo 1**: Próxima sequencial (não responde questões)
     - **Modo 2**: Aleatória não resolvida (responde com alternativa C)

4. **Aguarde o processamento** 
   - O navegador abrirá automaticamente
   - Navegue até uma página de questão do TEC
   - O bot começará a processar as questões

## ⚙️ Configuração

Você pode ajustar as configurações editando o arquivo `tecanki.py`:

```python
# Timeout e delays
TIMEOUT_ELEMENTO = 10
DELAY_COMENTARIO = 2.0
DELAY_NAVEGACAO = 2.5

# Anki
ANKI_ENDPOINT = "http://127.0.0.1:8765"
TIPO_NOTA = "Basic"
```

## 🛠️ Tecnologias

- **Selenium** - Automação do navegador
- **BeautifulSoup4** - Processamento de HTML
- **Requests** - Comunicação com AnkiConnect
- **Rich** - Interface de terminal bonita

⭐ Se este projeto te ajudou, considere dar uma estrela!
