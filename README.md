# Azul Logistica MCP

Servidor MCP para integrar operacoes da Azul Cargo Express com outros MCPs e sistemas,
como Shopify e TOTVS Moda.

## Estrutura

- `azul_mcp_server.py`: servidor MCP principal.
- `mcp_config.example.json`: exemplo de configuracao do client MCP.
- `requirements.txt`: dependencias do MCP.
- `.env.example`: modelo das credenciais e parametros da Azul (copie para `.env`).

## Configuracao

```bash
python -m pip install -r requirements.txt
cp .env.example .env   # preencha com as credenciais da sua conta Azul Cargo
```

Deixe `SiglaServico` **vazio** no `.env` para receber todos os servicos disponiveis.
Uma sigla especifica (ex.: `ECOMM_CORP`) pode filtrar e retornar zero cotacoes.

Por padrao o MCP usa a API de producao:

```text
https://ediapi.onlineapp.com.br/toolkit/api
```

Para apontar para homologacao ou outro ambiente, defina `AZUL_BASE_URL` no `.env`.

## Ferramentas MCP

- `azul_status_config`: valida configuracao local sem expor senha ou token.
- `azul_testar_autenticacao`: testa login na API Azul.
- `azul_listar_servicos`: lista as siglas de servico (PREMIUM, EXPRESSO, ECOMM, STANDARD e variantes CORP).
- `azul_cotar_frete`: gera cotacao de frete.
- `azul_rastrear`: consulta rastreio por AWB, NFe/CTe ou pedido.
- `azul_download_cte`: baixa XML/PDF de CTe por AWB.
- `azul_gerar_remessa`: emite AWB/Minuta usando payload da API Azul.
- `azul_cancelar_remessa`: cancela AWB/Minuta por AWB.
- `azul_buscar_faturas`: consulta faturas por periodo (`data_inicio`/`data_fim`),
  por `ano`+`mes` ou pelos `ultimos_meses`. Pode exigir liberacao da Azul.

As ferramentas que geram efeito operacional (`azul_gerar_remessa` e
`azul_cancelar_remessa`) usam `confirmar=False` por padrao. Nesse modo elas
retornam o endpoint e o payload sem executar a operacao. Para enviar para a Azul,
chame novamente com `confirmar=True`.

## Rodar

```bash
python azul_mcp_server.py
```

Para usar em um client MCP (Claude Desktop, etc.), copie `mcp_config.example.json`
e ajuste `command`, `args` e `cwd` para o caminho da sua maquina.
