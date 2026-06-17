# 🛢️ BioFuel Monitor

Painel automático de notícias sobre **SAF**, **Biobunker** e **Blending**, atualizado todos os dias via GitHub Actions — sem precisar de chave de API nem custo nenhum.

## Como configurar (uma vez só)

### 1. Crie um repositório no GitHub
- Acesse [github.com/new](https://github.com/new)
- Nome: `biofuel-monitor`
- Marque **Public**
- Clique em **Create repository**

### 2. Suba estes arquivos
Envie todos os arquivos desta pasta para o repositório (incluindo a pasta `.github/workflows`):
- `build.py`
- `requirements.txt`
- `.github/workflows/update.yml`

No GitHub, use **Add file → Upload files** e arraste a pasta toda.

### 3. Ative o GitHub Pages
- Vá em **Settings → Pages**
- Em **Source**, selecione **Deploy from a branch**
- Branch: **main**, pasta: **/ (root)**
- Clique em **Save**

### 4. Rode a primeira atualização manualmente
- Vá na aba **Actions** do repositório
- Clique em **Atualizar BioFuel Monitor** → **Run workflow** → **Run workflow**
- Aguarde ~1 minuto até aparecer o ✅ verde

### 5. Acesse seu painel
Seu site estará disponível em:
```
https://SEU-USUARIO.github.io/biofuel-monitor/
```

Salve esse link como favorito no celular — ele atualiza automaticamente todo dia às 07h UTC (04h em Brasília).

## Atualizar manualmente quando quiser
Vá em **Actions → Atualizar BioFuel Monitor → Run workflow** a qualquer momento.

## Personalizar os temas de busca
Edite as queries dentro de `build.py`, na lista `FEEDS`, para ajustar as palavras-chave de cada categoria.
