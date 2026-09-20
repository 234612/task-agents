import requests
from bs4 import BeautifulSoup
# from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.tools import tool
from requests.exceptions import RequestException
from urllib3.exceptions import InsecureRequestWarning

# 禁用不安全请求警告（因为有些网站证书可能有问题）
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# --- 工具 1: 深度网络搜索 ---
# 专为市场调研优化：获取更多结果，并搜索深度内容
# tavily_search = TavilySearchResults(
#     max_results=10,  # 增加结果数量，为分析提供更多素材
#     search_depth="advanced",  # 使用深度搜索模式，质量更高
#     name="web_search",
#     description=(
#         "用于在互联网上搜索最新信息、新闻、数据或特定事实。"
#         "适用于初步信息搜集、寻找竞品、获取行业概览。"
#         "返回结果包含标题、链接和内容摘要。"
#     )
# )


# --- 工具 2: 网页内容提取器 ---
@tool
def extract_web_content(url: str) -> str:
    """
    当需要深入阅读某个特定网页的详细内容时使用此工具。
    它会抓取页面的主要文本内容，过滤掉导航、广告等噪音。
    这对于分析竞品详情页、用户评论、技术文档或新闻报道至关重要。

    参数:
        url: 需要提取内容的网页完整链接。

    返回:
        网页的纯文本内容。如果抓取失败，会返回错误信息。
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        # 设置超时，防止被一个慢网站卡死
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        response.raise_for_status()

        # 尝试根据网页编码解码，如果失败则使用 apparent_encoding
        response.encoding = response.apparent_encoding

        soup = BeautifulSoup(response.text, 'html.parser')

        # 移除 script 和 style 标签及其内容
        for script_or_style in soup(["script", "style", "nav", "footer", "header"]):
            script_or_style.decompose()

        # 获取纯文本，并用换行符分隔
        text_content = soup.get_text(separator='\n', strip=True)

        # 简单清洗：移除过多的空行
        lines = (line.strip() for line in text_content.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text_content = '\n'.join(chunk for chunk in chunks if chunk)

        # 限制返回长度，防止Token爆炸 (例如限制在15000字符)
        return text_content[:15000] + "..." if len(text_content) > 15000 else text_content

    except RequestException as e:
        return f"请求错误: 无法访问 {url}。错误信息: {str(e)}"
    except Exception as e:
        return f"处理错误: 在处理 {url} 时发生未知错误。错误信息: {str(e)}"


# --- 工具集合导出 ---
# 主 Agent 或子 Agent 可以直接导入这个列表
WEB_RESEARCH_TOOLS = [extract_web_content]