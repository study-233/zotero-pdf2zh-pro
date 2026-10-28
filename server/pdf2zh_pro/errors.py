"""Safe diagnostic classification for providers and PDF tasks."""
import ssl
from pdf2zh_pro.types import DiagnosticMessage
from pdf2zh_pro.translator.openai_protocol import protocol_rejection_message

def explain_service_error(error: Exception | str) -> str:
    message = str(error).strip() if not isinstance(error, str) else error.strip()
    if not message:
        return "Unknown translation error"

    # SDK connection exceptions hide their TLS cause behind "Connection error".
    # Classify the chain without exposing request URLs, headers or credentials.
    cause = error if isinstance(error, Exception) else None
    seen = set()
    tls_failure = False
    forbidden = False
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        tls_failure |= isinstance(cause, ssl.SSLCertVerificationError)
        forbidden |= getattr(cause, "status_code", None) == 403
        cause = cause.__cause__ or cause.__context__
    lowered = message.lower()
    if tls_failure or "certificate_verify_failed" in lowered or "certificate verify failed" in lowered:
        return (
            "TLS 证书验证失败：Python 服务无法验证 API 端点的证书链。"
            "请使用系统信任证书，并检查代理或中转站的证书配置。"
        )
    if (forbidden or "403" in lowered) and any(
        phrase in lowered.replace(" ", "")
        for phrase in ("standardcodexclient", "标准codex客户端", "client_not_allowed", "client_restricted")
    ):
        return (
            "中转站拒绝此客户端（403）：此通道限制客户端类型。"
            "请联系服务商开放第三方应用调用，或使用允许翻译插件调用的通道。"
        )

    if "font asset download failed" in message.lower():
        return (
            "字体资源下载失败。已完成的字体会保留，请检查网络后重试任务。"
            f"详情：{message}"
        )

    if "object has no attribute 'choices'" in message or 'object has no attribute "choices"' in message:
        return (
            "LLM 接口返回的不是标准 OpenAI Chat Completions 响应。"
            "请检查 apiUrl 或路径，确认它指向兼容的 /chat/completions 接口。"
        )

    return message


def diagnose_service_error(error: Exception | str) -> list[dict[str, str]]:
    message = explain_service_error(error)
    lowered = message.lower()

    diagnostics: list[DiagnosticMessage] = []
    if "chatgpt_sign_in_required" in lowered or any(text in message for text in (
        "ChatGPT 登录已失效", "ChatGPT 授权已失效", "ChatGPT 授权无法续期", "请先登录 ChatGPT",
    )):
        return [DiagnosticMessage(
            code="chatgpt_sign_in_required", severity="error",
            message="ChatGPT 登录已失效，请重新登录。",
            suggestion="点击“重新登录”，在浏览器完成授权后重试翻译。",
        ).to_dict()]
    if "tls 证书验证失败" in lowered:
        return [DiagnosticMessage(
            code="network_tls", severity="error",
            message="Python 服务验证 API 端点的 TLS 证书失败。",
            suggestion="更新服务端并使用系统信任证书；检查代理证书和中转站证书链。",
        ).to_dict()]
    if "中转站拒绝此客户端" in message:
        return [DiagnosticMessage(
            code="llm_client_restricted", severity="error",
            message="中转站不允许当前客户端调用此通道。",
            suggestion="联系服务商开放第三方应用权限，或选择允许插件调用的通道。",
        ).to_dict()]
    protocol_rejected = protocol_rejection_message(message)
    if protocol_rejected:
        diagnostics.append(DiagnosticMessage(
            code="llm_protocol", severity="error",
            message="中转站明确拒绝了当前 API 协议。",
            suggestion="将 API 协议设为自动识别，或手动选择服务商支持的 Responses / Chat Completions。",
        ))
    if "字体资源下载失败" in message or "font asset download failed" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="font_asset_download",
                severity="error",
                message="首次运行所需字体未能全部下载。",
                suggestion="检查网络或代理设置后重试任务；已下载完成的字体不会重复下载。",
            )
        )
    if "too many cid paragraphs" in lowered or "cid" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="pdf_text_layer_cid",
                severity="warning",
                message="PDF text layer appears to contain CID-heavy encoded text.",
                suggestion=(
                    "Try enabling OCR workaround first. If the PDF renders normally "
                    "but still fails, enable skip text safety checks for this file."
                ),
            )
        )
    if not protocol_rejected and any(word in lowered for word in ("chat/completions", "choices", "responses", "译文", "协议")):
        diagnostics.append(
            DiagnosticMessage(
                code="llm_response_shape",
                severity="error",
                message="LLM 接口未返回所选协议要求的完整文本响应。",
                suggestion=(
                    "检查 API 地址、Chat Completions / Responses 协议及输出 token 上限。"
                ),
            )
        )
    if not protocol_rejected and "model" in lowered and any(word in lowered for word in ("not found", "not exist", "unavailable", "not supported")):
        diagnostics.append(DiagnosticMessage(
            code="llm_model", severity="error", message="所选模型不可用或不存在。",
            suggestion="核对中转站提供的模型名称和当前密钥的模型权限。",
        ))
    if any(word in lowered for word in ("requestoptions", "请求参数", "unsupported parameter", "unknown parameter")):
        diagnostics.append(DiagnosticMessage(
            code="llm_parameters", severity="error", message="API 请求参数无效或不受支持。",
            suggestion="检查额外请求参数及所选协议；移除服务不支持的可选参数。",
        ))
    if "401" in lowered or "unauthorized" in lowered or "api key" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="llm_auth",
                severity="error",
                message="The provider rejected the configured credentials.",
                suggestion="Check the API key, provider account status, and selected service.",
            )
        )
    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="llm_rate_limit",
                severity="warning",
                message="The provider appears to be rate limiting requests.",
                suggestion="Lower QPS or pool size, then retry the task.",
            )
        )
    if "timeout" in lowered or "timed out" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="network_connectivity",
                severity="warning",
                message="API 端点未在超时期限内响应。",
                suggestion=(
                    "稍后重试或更换稳定端点；若服务本身响应较慢，"
                    "可在内部配置参数中设置 openai_timeout。"
                ),
            )
        )
    elif "connection" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="network_connectivity",
                severity="warning",
                message="The server had trouble reaching the configured endpoint.",
                suggestion="Check the API URL, local proxy/VPN, and provider availability.",
            )
        )
    if "did not produce" in lowered or "translated pdf not found" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="output_missing",
                severity="error",
                message="pdf2zh_pro finished without the requested output file.",
                suggestion="Retry with one output mode first, then check server logs if it repeats.",
            )
        )
    if "removes every page" in lowered:
        diagnostics.append(
            DiagnosticMessage(
                code="invalid_page_range",
                severity="error",
                message="The skip-last-pages setting would remove the whole PDF.",
                suggestion="Reduce skipLastPages or set it to 0.",
            )
        )

    if not diagnostics:
        diagnostics.append(
            DiagnosticMessage(
                code="unknown_error",
                severity="error",
                message="The server could not classify this error automatically.",
                suggestion="Copy diagnostics and server logs when reporting the issue.",
            )
        )

    return [diagnostic.to_dict() for diagnostic in diagnostics]
