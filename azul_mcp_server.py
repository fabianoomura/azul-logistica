import base64
import calendar
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
import xml.etree.ElementTree as ET

import requests
from requests import HTTPError
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP


load_dotenv()

mcp = FastMCP("azul-logistica")


class AzulCargoClient:
    def __init__(self) -> None:
        self.base_url = os.getenv(
            "AZUL_BASE_URL",
            "https://ediapi.onlineapp.com.br/toolkit/api",
        ).rstrip("/")
        self.email = os.getenv("Email")
        self.senha = os.getenv("Senha")
        self.cnpj = os.getenv("CNPJ")
        self.cnpj_corrido = only_digits(os.getenv("CNPJ_CORRIDO", ""))
        self.base_origem = os.getenv("BaseOrigem", "")
        self.cep_origem = only_digits(os.getenv("CEP_ORIGEM", ""))
        self.sigla_servico = os.getenv("SiglaServico", "")
        self.taxa_manuseio = float(os.getenv("TAXA_MANUSEIO", "0") or 0)
        self.token: str | None = None
        self.token_expiry: datetime | None = None

    def authenticate(self) -> str:
        if not self.email or not self.senha:
            raise ValueError("Email e Senha devem estar configurados no .env")

        response = requests.post(
            f"{self.base_url}/Autenticacao/AutenticarUsuario",
            json={"Email": self.email, "Senha": self.senha},
            timeout=30,
        )
        raise_for_status_with_body(response)
        data = response.json()

        if data.get("HasErrors", True):
            raise RuntimeError(data.get("ErrorText") or "Erro desconhecido na autenticacao")

        token = data.get("Value")
        if not token:
            raise RuntimeError("Token nao retornado pela API da Azul")

        self.token = token
        self.token_expiry = datetime.now() + timedelta(hours=8)
        return token

    def get_token(self) -> str:
        if not self.token or not self.token_expiry or datetime.now() >= self.token_expiry:
            return self.authenticate()
        return self.token

    def post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/{endpoint}",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        raise_for_status_with_body(response)
        return response.json()

    def authenticated_post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload_with_token = {"Token": self.get_token(), **payload}
        return self.post(endpoint, payload_with_token)


def only_digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def raise_for_status_with_body(response: requests.Response) -> None:
    try:
        response.raise_for_status()
    except HTTPError as exc:
        detail = response.text.strip()
        if detail:
            raise HTTPError(f"{exc}; response body: {detail}", response=response) from exc
        raise


def validate_cep(cep: str, field_name: str) -> str:
    digits = only_digits(cep)
    if len(digits) != 8:
        raise ValueError(f"{field_name} deve ter 8 digitos")
    return digits


def calculate_cubic_weight(item: dict[str, Any]) -> float:
    volume = int(item.get("volume", item.get("Volume", 1)) or 1)
    altura = float(item.get("altura", item.get("Altura")))
    largura = float(item.get("largura", item.get("Largura")))
    comprimento = float(item.get("comprimento", item.get("Comprimento")))
    return round((altura * largura * comprimento * volume) / 6000, 2)


def normalize_items(items: list[dict[str, Any]], peso_total: float) -> list[dict[str, Any]]:
    if not items:
        raise ValueError("Informe pelo menos um item/volume")

    total_units = sum(int(item.get("volume", item.get("Volume", 1)) or 1) for item in items)
    normalized = []

    for item in items:
        volume = int(item.get("volume", item.get("Volume", 1)) or 1)
        if volume < 1:
            raise ValueError("O volume de cada item deve ser pelo menos 1")

        peso = item.get("peso", item.get("Peso"))
        if peso is None:
            peso = peso_total * (volume / total_units)

        normalized.append(
            {
                "Volume": volume,
                "Peso": round(float(peso), 3),
                "Altura": float(item.get("altura", item.get("Altura"))),
                "Comprimento": float(item.get("comprimento", item.get("Comprimento"))),
                "Largura": float(item.get("largura", item.get("Largura"))),
            }
        )

    return normalized


def summarize_quotes(response: dict[str, Any], handling_fee: float = 0) -> dict[str, Any]:
    quotes = response.get("Value") or []
    if not isinstance(quotes, list):
        return response

    summarized = []
    for quote in quotes:
        total_api = float(quote.get("Total", 0) or 0)
        summarized.append(
            {
                "nome_servico": quote.get("NomeServico"),
                "frete": quote.get("Frete"),
                "total_api": total_api,
                "taxa_manuseio": handling_fee,
                "total_com_manuseio": round(total_api + handling_fee, 2),
                "prazo_dias": quote.get("Prazo"),
                "peso_taxado": quote.get("PesoTaxado"),
                "raw": quote,
            }
        )

    return {
        "HasErrors": response.get("HasErrors", False),
        "ErrorText": response.get("ErrorText"),
        "Value": summarized,
        "raw": response,
    }


def extract_cte_info(xml_content: str) -> dict[str, Any]:
    xml_clean = xml_content.replace(' xmlns="http://www.portalfiscal.inf.br/cte"', "")
    root = ET.fromstring(xml_clean)

    def text(path: str, default: str = "") -> str:
        return root.findtext(path, default)

    return {
        "numero_cte": text(".//ide/nCT"),
        "serie": text(".//ide/serie"),
        "data_emissao": text(".//ide/dhEmi"),
        "cfop": text(".//ide/CFOP"),
        "natureza_operacao": text(".//ide/natOp"),
        "origem": {
            "municipio": text(".//ide/xMunIni"),
            "uf": text(".//ide/UFIni"),
        },
        "destino": {
            "municipio": text(".//ide/xMunFim"),
            "uf": text(".//ide/UFFim"),
        },
        "emitente": {
            "cnpj": text(".//emit/CNPJ"),
            "nome": text(".//emit/xNome"),
            "ie": text(".//emit/IE"),
        },
        "destinatario": {
            "cnpj": text(".//dest/CNPJ"),
            "nome": text(".//dest/xNome"),
        },
        "valores": {
            "total_servico": text(".//vPrest/vTPrest"),
            "valor_receber": text(".//vPrest/vRec"),
            "valor_carga": text(".//infCarga/vCarga"),
            "icms": text(".//ICMS00/vICMS"),
        },
        "modal_aereo": {
            "numero_oca": text(".//aereo/nOCA"),
            "data_prevista": text(".//aereo/dPrevAereo"),
            "dimensoes": text(".//aereo/natCarga/xDime"),
            "classe_tarifa": text(".//aereo/tarifa/CL"),
            "valor_tarifa": text(".//aereo/tarifa/vTar"),
        },
    }


client = AzulCargoClient()


@mcp.tool()
def azul_status_config() -> dict[str, Any]:
    """Mostra se as variaveis essenciais da API Azul Cargo estao configuradas."""
    return {
        "base_url": client.base_url,
        "email_configurado": bool(client.email),
        "senha_configurada": bool(client.senha),
        "cnpj": client.cnpj,
        "cnpj_corrido_configurado": bool(client.cnpj_corrido),
        "base_origem": client.base_origem,
        "cep_origem": client.cep_origem,
        "sigla_servico": client.sigla_servico,
        "taxa_manuseio": client.taxa_manuseio,
    }


@mcp.tool()
def azul_testar_autenticacao() -> dict[str, Any]:
    """Testa a autenticacao na API Azul Cargo sem expor o token completo."""
    token = client.get_token()
    return {
        "ok": True,
        "token_preview": f"{token[:8]}...",
        "token_expira_em": client.token_expiry.isoformat() if client.token_expiry else None,
    }


@mcp.tool()
def azul_cotar_frete(
    cep_destino: str,
    valor_total: float,
    peso_real: float,
    itens: list[dict[str, Any]],
    cep_origem: str | None = None,
    base_origem: str | None = None,
    base_destino: str = "",
    tipo_entrega: Literal["DOMICILIO", "AEROPORTO"] = "DOMICILIO",
    sigla_servico: str | None = None,
    pedido: str = "",
    taxa_coleta: bool = True,
    coleta: bool = True,
    aplicar_taxa_manuseio: bool = True,
) -> dict[str, Any]:
    """Cota frete na Azul Cargo. Itens usam altura/largura/comprimento em cm e volume opcional."""
    cep_origem_final = validate_cep(cep_origem or client.cep_origem, "cep_origem")
    cep_destino_final = validate_cep(cep_destino, "cep_destino")
    normalized_items = normalize_items(itens, peso_real)
    peso_cubado = round(sum(calculate_cubic_weight(item) for item in normalized_items), 2)
    quantidade_volumes = sum(item["Volume"] for item in normalized_items)

    payload = {
        "BaseOrigem": base_origem if base_origem is not None else client.base_origem,
        "CEPOrigem": cep_origem_final,
        "BaseDestino": base_destino,
        "CEPDestino": cep_destino_final,
        "PesoCubado": peso_cubado,
        "PesoReal": float(peso_real),
        "Volume": quantidade_volumes,
        "ValorTotal": float(valor_total),
        "Pedido": pedido,
        "SiglaServico": sigla_servico if sigla_servico is not None else client.sigla_servico,
        "TaxaColeta": taxa_coleta,
        "TipoEntrega": tipo_entrega,
        "Coleta": coleta,
        "Itens": normalized_items,
    }
    response = client.authenticated_post("Cotacao/Enviar", payload)
    handling_fee = client.taxa_manuseio if aplicar_taxa_manuseio else 0
    result = summarize_quotes(response, handling_fee)
    result["payload_calculado"] = {
        "peso_cubado": peso_cubado,
        "peso_real": float(peso_real),
        "peso_taxavel_referencia": max(float(peso_real), peso_cubado),
        "volume": quantidade_volumes,
        "itens": normalized_items,
    }
    return result


@mcp.tool()
def azul_listar_servicos() -> dict[str, Any]:
    """Lista as siglas de servico da Azul Cargo (tabela vigente desde 23/04/2023).

    Use a sigla em azul_cotar_frete (parametro sigla_servico). Deixar vazio retorna
    todos os servicos disponiveis para a rota; uma sigla especifica filtra e pode
    retornar zero cotacoes se aquele servico nao atender a rota.
    """
    return {
        "vigente_desde": "2023-04-23",
        "observacao": (
            "SiglaServico vazio na cotacao retorna todos os servicos disponiveis. "
            "Uma sigla especifica filtra e pode zerar o resultado."
        ),
        "servicos": {
            "PREMIUM": "PREMIUM",
            "PREMIUM_CORP": "PREMIUM CORP",
            "EXPRESSO": "EXPRESSO",
            "EXPRESSO_CORP": "EXPRESSO CORP",
            "ECOMM": "E-COMM",
            "ECOMM_CORP": "E-COMM CORP",
            "STANDARD": "STANDARD",
            "STANDARD_CORP": "STANDARD CORP",
        },
        "descontinuados": ["2 HORAS", "AMANHA", "AMANHA_CORP"],
    }


@mcp.tool()
def azul_rastrear(
    awb: str = "",
    chave_nfe: str = "",
    pedido: str = "",
) -> dict[str, Any]:
    """Rastreia envio por AWB, chave NFe/CTe de 44 digitos ou numero de pedido."""
    if not any([awb, chave_nfe, pedido]):
        raise ValueError("Informe awb, chave_nfe ou pedido")

    chave_limpa = only_digits(chave_nfe)
    if chave_nfe and len(chave_limpa) != 44:
        raise ValueError("chave_nfe deve ter 44 digitos")

    return client.authenticated_post(
        "Rastreio/Consultar",
        {
            "Awb": str(awb),
            "ChaveNfe": chave_limpa,
            "Pedido": str(pedido),
        },
    )


@mcp.tool()
def azul_download_cte(
    awb: str,
    formato: Literal["xml", "pdf"] = "xml",
    salvar_arquivo: bool = False,
) -> dict[str, Any]:
    """Baixa CTe por AWB em XML ou PDF. PDF volta em base64; se salvar_arquivo=True grava em downloads_cte."""
    endpoint = "DownloadCte/DownloadXML" if formato == "xml" else "DownloadCte/DownloadPDF"
    response = client.authenticated_post(endpoint, {"Awb": str(awb)})

    value = response.get("Value")
    result: dict[str, Any] = {"response": response, "arquivo_salvo": None}

    if response.get("HasErrors") or not value:
        return result

    if formato == "xml":
        try:
            result["cte_info"] = extract_cte_info(value)
        except ET.ParseError as exc:
            result["cte_info_error"] = str(exc)

    if salvar_arquivo:
        downloads_dir = Path.cwd() / "downloads_cte"
        downloads_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = "xml" if formato == "xml" else "pdf"
        path = downloads_dir / f"CTe_AWB_{awb}_{timestamp}.{extension}"
        if formato == "xml":
            path.write_text(value, encoding="utf-8")
        else:
            path.write_bytes(base64.b64decode(value))
        result["arquivo_salvo"] = str(path)

    return result


@mcp.tool()
def azul_gerar_remessa(
    payload: dict[str, Any],
    tipo: Literal["nfe_declaracao", "cte", "azulbox"] = "nfe_declaracao",
    confirmar: bool = False,
) -> dict[str, Any]:
    """Gera AWB/Minuta na Azul. Com confirmar=False apenas valida e retorna o payload que seria enviado."""
    endpoints = {
        "nfe_declaracao": "EmissaoAWB/Enviar",
        "cte": "EmissaoAWB/EnviarCte",
        "azulbox": "AzulBox/Enviar",
    }
    endpoint = endpoints[tipo]
    payload_sem_token = {key: value for key, value in payload.items() if key.lower() != "token"}

    if not confirmar:
        return {
            "confirmar_necessario": True,
            "endpoint": endpoint,
            "payload": payload_sem_token,
            "observacao": "Reenvie com confirmar=True para emitir a AWB/Minuta na Azul.",
        }

    return client.authenticated_post(endpoint, payload_sem_token)


@mcp.tool()
def azul_cancelar_remessa(
    awb: str,
    confirmar: bool = False,
) -> dict[str, Any]:
    """Cancela uma AWB/Minuta na Azul. Com confirmar=False apenas mostra a operacao."""
    awb_limpa = only_digits(awb)
    if not awb_limpa:
        raise ValueError("Informe o AWB para cancelamento")

    payload = {"Awb": awb_limpa}
    endpoint = "CancelamentoMinuta/Cancelar"

    if not confirmar:
        return {
            "confirmar_necessario": True,
            "endpoint": endpoint,
            "payload": payload,
            "observacao": "Reenvie com confirmar=True para cancelar a AWB/Minuta na Azul.",
        }

    return client.authenticated_post(endpoint, payload)


@mcp.tool()
def azul_buscar_faturas(
    data_inicio: str = "",
    data_fim: str = "",
    ano: int | None = None,
    mes: int | None = None,
    ultimos_meses: int | None = None,
    cnpj: str = "",
) -> dict[str, Any]:
    """Busca faturas na Azul (endpoint FaturasOnline; pode exigir liberacao da Azul).

    Informe o periodo de UMA destas formas:
    - data_inicio e data_fim no formato YYYY-MM-DD; ou
    - ano e mes (1-12) para o mes inteiro; ou
    - ultimos_meses (N) para os ultimos N meses ate hoje.
    """
    if ano is not None and mes is not None:
        if not 1 <= mes <= 12:
            raise ValueError("mes deve estar entre 1 e 12")
        data_inicio = f"{ano}-{mes:02d}-01"
        ultimo_dia = calendar.monthrange(ano, mes)[1]
        data_fim = f"{ano}-{mes:02d}-{ultimo_dia:02d}"
    elif ultimos_meses is not None:
        if ultimos_meses < 1:
            raise ValueError("ultimos_meses deve ser >= 1")
        fim = datetime.now()
        inicio = fim - timedelta(days=ultimos_meses * 30)
        data_inicio = inicio.strftime("%Y-%m-%d")
        data_fim = fim.strftime("%Y-%m-%d")
    elif data_inicio and data_fim:
        datetime.strptime(data_inicio, "%Y-%m-%d")
        datetime.strptime(data_fim, "%Y-%m-%d")
    else:
        raise ValueError("Informe data_inicio+data_fim, ou ano+mes, ou ultimos_meses")

    return client.authenticated_post(
        "FaturasOnline/BuscarFaturas",
        {
            "DataInicio": data_inicio,
            "DataFim": data_fim,
            "Cnpj": only_digits(cnpj) or client.cnpj_corrido,
        },
    )


if __name__ == "__main__":
    mcp.run()
