# -*- coding: utf-8 -*-
"""
live.py —— 生成影视仓 / TVBox 用的直播源（live.txt 主输出 + live.m3u 兼容）

本版修掉的问题（对应"点 CCTV3 出来是卖药广告、偶尔又正常"）：
1. 旧版 rank() 判断的是"流地址里有没有 cctv 字样"，可那个字符串是流地址，
   不是上游列表名。结果：把自己把文件命名成 cctv3.m3u8 的灰产转播服务器排到
   第 1 条（影视仓默认只播第 1 条），而运营商正规源排在 2、3 条。现在改为按
   "上游可信度 + 流本身是否真在直播"排序。
2. 旧版 probe() 只要地址能返回 #EXTM3U 就算活：master 里子流全死的、以及
   带 #EXT-X-ENDLIST 的点播文件（广告/电影循环）统统通过。现在做三层校验：
   能拿到节目单 → 最后一个切片真的能下载 → 不是已经结束的点播文件 / 不是
   定格不动的僵尸源。
3. 旧版靠显示名归并，'CCTV-3'、'CCTV3高清'、'中央电视台综艺频道' 会变成三个
   不同频道，线路被打散。现在统一映射成规范名（CCTV3）后再归并。
4. 新增交叉校验：同一个地址在不同上游被标成不同频道 → 视为贴错标签，丢弃；
   地址里写着 cctv5 却标成 CCTV3 → 丢弃。这类"换台"在免费源里非常常见。
5. 新增"终审"：间隔十几秒抓两次节目单，只有切片真的在往前滚才算在直播。广告 /
   电影循环源是固定文件（要么带 ENDLIST，要么序列不动），一律丢弃，不再留作备胎。
6. 三级优先选线（这是本版最关键的改动）：
     ① 可信上游 + 已验证在直播
     ② 可信上游 + 这次测不到（GitHub 的机器在境外，连不上国内源是常态）
     ③ 弱上游   + 已验证        ← 只在前两级都拿不到时用，日志点名
   为什么要把②排在③前面：CI 能测通的往往只剩 iptv-org 这类贴错标签的源，
   而那正是"点 CCTV3 出来是卖药广告"的来源。宁可给一条没验证过的国内可信源。
   第②级只对央视/卫视/CGTN 开放 —— 地方台上千个，全放进来会塞满打不开的地址。
7. 上游列表会被体检：条数过少、或里面一条央视都没有，判为"内容异常"并按最差权重
   处理，同时打印 [上游检查]。fanmingming 的仓库路径和他域名下的 iptv.m3u 在
   2026-11 都已 404，正是这种静默失效让央视整组消失的。
9. 清洗 TVBox txt 风格的地址尾缀（`http://x/y.m3u8$LR•IPV4『线路138』`），并丢掉
   rtp/udp 组播地址。YueChan 列表里 CCTV3~17 全是 rtp://239.x.x.x，影视仓根本
   播不了 —— 这是央视整组缺失的第二个原因（第一个是上游 404）。
10. 纯英文名的境外小台（HarbinComprehensiveNewsChannel 之类）直接丢弃，把测活
   名额留给央视和卫视。
12. 修掉"点 CCTV1 播出 CCTV5+"（这是 2026-11 用户报的最后一个问题）：
    a) 台号校验原来只扫 URL 的 path，对 `migu.php?id=cctv5p` 这种把台号写在 query
       里的地址等于完全没校验。现在扫整条地址，并支持 5+/5p/plus。
    b) 台号冲突不再"全丢"：`id=cctv5p` 被同时标成 CCTV1 和 CCTV5+ 时，让地址自己
       作证，归给台号对得上的那个，避免把 CCTV5+ 唯一正确的源一起误杀。
    c) 排序按"自证等级"分档：路径自证（/cctv1/、/tsfile/live/0001_1）> 仅 query
       自证（live.php?id=CCTV1）> 看不出来。单入口脚本一台分发九个台是串台高发
       结构，自然被压到后面。
    d) 日志会列出 [存疑]（央视却没有台号线索）和 [批量源]（一个入口分发多个台），
       这两类点开最容易发现串台。
13. BLOCK_HOSTS：已知串台的主机写进这个常量即可永久拉黑，不用等上游修。
14. 2026-11-24 修"央视主台全走未验证兜底、内容无人确认"的问题：
    a) 排序从"一堆加减法"改成分档 (档位, 档内细项)。档位顺序：可信+已验证 >
       可信+裸TS > 可信+未验证 > 弱源+已验证 > 弱源+裸TS。档与档绝对成立，
       不会再出现某个加分项把未验证的源顶到已验证前面。
    b) [未验证] 日志会算出其中的央视/卫视各几个。那才是真正的风险面：CI 在境外，
       央视主台往往整组落进这一档，点开串台的概率就集中在这里。
    c) 注意：这一版曾经加过"上游共识"权重（同一条地址被越多列表收录越优先），
       实测证明是错的，已于当晚删除 —— 见 score() 的 docstring。
15. 2026-11-24 晚：一个台放多条播放源（用户要求"能自己选"+"别在列表里铺开"）。
    用户的实际观察是："不会直接从 CCTV1 跳到 CCTV5+ 了，只是把跳转时间放慢了"。
    这恰好说明了机制：影视仓播不出画面时**顺着播放列表往下走**，一个台只有 1 条源，
    它就没得选，只能掉到下一个频道 —— 5+ 在列表里离得很近，看着就像"CCTV1 变成了
    CCTV5+"。所以：
    a) 修掉 `[best][:PER_CH_LINES]` 这个 bug —— 它从一个单元素列表切片，导致
       PER_CH_LINES 写多少都只输出 1 条（workflow 里那个 '1' 从来没生效过）。
    b) 一个台测完有几条能用的就放几条，**不设上限**。用户的原话是"只要搜到有源就
       加在台里面"。
    c) 显示名不加"线路2/3"：同一个台的几条源 tvg-id / tvg-name / 显示名三者完全
       相同。★ 当时的推测是"影视仓会把它们并成一个台的多条播放源"，**这个推测被
       实测推翻了**（见 18 条）—— 影视仓对 m3u 不做合并，同名的每条 #EXTINF 都算
       一个独立频道，列表里就并排出现 2 CCTV2 / 3 CCTV2 / 4 CCTV3 / 5 CCTV3 …
    d) 候选名额相应加大（央视 12 / 卫视 8 / 地方 3），否则"有几条放几条"没料可放。
16. 2026-11-24 三改：把"用户网络实测可达"固化进代码。
    起因：用户反馈"CCTV 大部分台看不了，只有 CCTV5+、CCTV9 少数几个能看，前两天
    CCTV1/2/3 都能看"，并给出关键线索"只是把跳转时间放慢了"。
    查证过程（全部是实测，不是推断）：
    a) 翻出仓库历史 30 次提交逐条对比，发现 11-23 能给用户看的那版，CCTV1 用的是
       湖南广电 liveplay-srs.voc.com.cn（今日实测 0.18 秒返回 200、序列在滚），
       CCTV2/3/7 用 74.91.26.218:82（301 → 跟随重定向后 200）。
    b) 今天这版把 14 个央视台全指到 iptv.huuc.edu.cn（华北水利水电大学校园镜像），
       实测 8 秒超时 —— 只对教育网开放，任何家庭宽带都进不去。它能排到第一，正是
       因为 13(c) 那条"路径自证"给了它 -8 分（/hls/cctv1hd.m3u8 命名太规范），
       而正规广电 CDN 用内部编号（134_180adf），自证 0 分反被压下去。
       ★ 这就是"地址自证"权重的系统性陷阱：命名规范 ≠ 能播。
    c) 在上游 270 个主机里逐台实测：110 个可达、160 个不可达。而那 15 个"吃台大户"
       （t.061899.xyz 33 台、zby.130519.xyz 27 台、huuc 24 台…）**全部不可达**，
       它们占掉了当时输出的一半以上。已全部写进 BLOCK_HOSTS。
    d) 反过来，能播集中在少数主机：74.91.26.218 一个扛 CCTV1/2/3/7/10~14，
       120.76.248.139 扛 CCTV15/17/5+，浙江广电扛 CCTV13。已写进 PREFER_HOSTS，
       在 score() 里给最高档位 -1，**高于 CI 的"已验证"** —— 因为跑测机在境外，
       它眼里的验证结果和用户家里能不能播基本无关。
    e) 测活阶段对 PREFER 源开例外：CI 测不通也保留为 unverified，否则这些源会被
       境外机器判死、根本进不了候选。
    f) 探测源可达性时必须 curl -L 跟随 301，否则 74.91 会被误判为不可达 —— 这个坑
       让最初的分析完全跑偏，一度得出"只有 9 个央视台有源"的错误结论。
    仍然缺源的台：CCTV4 / CCTV5 / CCTV6 / CCTV4K —— 当前网络下上游确实没有可达地址，
    这是源的问题，不是代码能解决的。
18. 2026-11-24 四改：输出格式从 m3u 换成 TVBox 风格 TXT（用户截图反馈）。
    用户要的是"列表里只显示 CCTV1 / CCTV2 / CCTV3，CCTV1 有多个源时在「线路选择」
    里显示多个源供手动切换"。而 m3u 天生做不到这件事：
    a) 实测证据：影视仓对同名 EXTINF 不合并，列表里出现 2 CCTV2 / 3 CCTV2 /
       4~7 CCTV3 四个 / 9~10 CCTV5+ 两个 —— 一个台占好几行，又长又乱。
    b) TXT 的 `CCTV1,地址1#地址2#地址3` 才是"一个台多条线路"的标准写法：影视仓
       读成 1 个频道 + 3 条线路，列表一行，菜单里的「线路选择」就是 源1/源2/源3。
    c) 主输出改为 live.txt；同时保留 live.m3u（每台只写第一条源），老直播地址
       继续可用，不会因为换格式突然全黑。
    d) 兜底：万一某个版本的影视仓不认 # 分隔，地址里 # 之后的部分按 URL 规范属于
       fragment，不会被发给服务器 —— 最坏情况退回"只播第一条"，不会整台播不了。
19. 2026-11-24 五改：解决"每个台只有一个源"（用户刷新缓存后的新反馈）。
    列表已经不重复了、CCTV1/2 也能播了，但线路选择里只有「源1」。查下来是两件事：
    a) 上游列表只认 m3u —— parse() 遇到 TXT 直接返回 0 条，这些列表被判"失效上游"
       丢掉。而榴芒电视.txt 里光央视就有 233 条。现在两种格式都认，原始条目
       1221 → 2009 条，CCTV1 的候选 2 → 6 条。
    b) 只有"测通了"的才写进线路，可境外 CI 一条都测不通中国移动的 CDN。
       现在央视/卫视改成"候选池里除了确认是广告/定格的，全部写上"，最多 6 条；
       地方台维持只写能播的（否则上千个台各挂 5 个死地址，列表几万行）。
    c) 补齐上游：suxuang 的榴芒电视/移动IPTV、wwb521 的 tv.txt。
    d) 全量重测 85 个主机，发现三个新大户 —— 192.151.150.154（51 台）、
       198.204.228.26（44 台）、47.97.252.137:8088（29 台），连同 38.75.136.137、
       63.141.230.178 一起写进 PREFER_HOSTS（已用隔 12 秒抓两次序列验证是真直播）。
       结果：CCTV1~17 每台 6 条线路、全部可达，排在最前的是 198.204.228.26。
    e) 之前"缺源的 CCTV4/5/6/4K"这次全部补齐：CCTV5 从 0 条可达变成 5 条。
21. 2026-11-25 六改：用户实测反馈"每个台还是只有一个源"+"CCTV5/CCTV6 播不了"
    +"CCTV1 没画面而 CCTV20 播的是 CCTV1"。四个独立问题，逐个查实：
    a) **`#` 分隔多源在这个版本的影视仓里不生效**。用户实测：live.txt 里写了 6 条，
       线路选择里仍只有「源1」。原理：`#` 在 URL 规范里是 fragment，播放器请求时
       把后面整段丢掉，永远只用得到第一条。改为 **同一个台名写多行**（每行一个
       地址）—— txt 解析器遇到同名频道会复用 Channel 对象并 addUrl，这才是多线路。
    b) **"能连上"不等于"能播"**。昨晚写进文件的那批源（198.204.228.26 /
       192.151.150.154 / 47.97.252.137 / 74.91 / 38.75 / 63.141）今早全部变成
       "播放列表 200、切片是 479 字节 HTML 错误页"。全量重测 547 条央视/卫视候选，
       **只有 26 条的切片是真 TS**。已把这批集体失效的主机从 PREFER_HOSTS 删除
       （它们是同一运营者的多个入口，74.91 根目录写着"服务器升级及付费服务公告"）。
    c) **编号模板补源**：`124.165.251.82:85/tsfile/live/%04d_1.m3u8` 这种路径里
       编号就是台号，实测 0001~0015 全部真 TS，但上游列表只抄了 0008/0012 两条
       —— 于是 CCTV1/3/5/6/7/11 在"上游查无此源"，用户那边就是"没画面"。
       现在按台号直接生成候选（另一台同构的 222.169.85.8:9901 一并加上）。
    d) **CCTV20 播 CCTV1**：那条是百度直播的用户流（p2.bdstatic.com/stream_bduid_xxx），
       本质是有人在转播 CCTV1，上游贴了"CCTV20"标签。已写进 BLOCK_HOSTS。
    e) score() 的优先级从"主机可不可信"改成"这条到底能不能播"：
       切片验证通过(live) > 裸TS > 未验证。PREFER 只在同档内加分，不再无条件置顶
       —— 之前的写法让"PREFER 主机上的死源"排在"真能播的源"前面，用户得一直往下切。
22. 只依赖本文件，不需要任何额外的配置文件。
"""

import os
import re
import sys
import time
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urljoin

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

try:                      # 关掉"自签证书"的告警刷屏
    import urllib3

    urllib3.disable_warnings()
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 主输出：TVBox / 影视仓 风格 TXT。一个台只占一行，多条播放源用 # 连在同一个台名后面：
#     CCTV1,http://a#http://b#http://c
# 影视仓读出来就是"一个 CCTV1 + 3 条线路"：列表里只有一行台名，播放时按菜单键进
# 「线路选择」能看到 源1 / 源2 / 源3，自己点着切。
#
# 为什么不用 m3u 干这件事：m3u 里同一个台写多条 #EXTINF，影视仓不会合并，而是当成
# 多个频道并列显示 —— 用户实测的列表就变成了
#     2 CCTV2 / 3 CCTV2 / 4 CCTV3 / 5 CCTV3 / 6 CCTV3 / 7 CCTV3 ...
# 一个台占四行，又长又乱。TXT 的 `台名,地址1#地址2` 才是"一台多线"的标准写法。
OUTPUT = os.path.join(ROOT, "live.txt")
BACKUP = os.path.join(ROOT, "live.txt.bak")

# 兼容输出：老的直播地址（…/live.m3u）继续可用，每台只写第一条源，带台标和分组。
OUTPUT_M3U = os.path.join(ROOT, "live.m3u")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "*/*"}

# (上游地址, 可信度) 可信度数字越小越可靠
# 2026-11 实测：fanmingming/live 仓库里已经没有 tv/iptv.m3u 了（raw 直接 404，
# 他的 tv/ 目录只剩台标 png），列表搬到了自己的域名 live.fanmingming.cn。
# 所以这里同时挂多个来源，任何一个挂掉都不该让某个频道组整体消失。
UPSTREAMS = [
    # 国内维护、以 IPv4 单播为主的列表，优先用（2026-11 实测均可解析到 20+ 个央视台）
    ("https://raw.githubusercontent.com/suxuang/myIPTV/main/ipv4.m3u", 1),
    ("https://cdn.jsdelivr.net/gh/suxuang/myIPTV@main/ipv4.m3u", 1),
    ("https://raw.githubusercontent.com/wwb521/live/main/tv.m3u", 1),
    ("https://cdn.jsdelivr.net/gh/wwb521/live@main/tv.m3u", 1),
    ("https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u", 2),
    ("https://cdn.jsdelivr.net/gh/YueChan/Live@main/IPTV.m3u", 2),
    ("https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u", 3),
    ("https://cdn.jsdelivr.net/gh/vbskycn/iptv@master/tv/iptv4.m3u", 3),
    # 同一个维护者的其他集合。实测这些文件和 ipv4.m3u 的重合度不高：加进来以后
    # CCTV1 的候选 2 → 5 条、CCTV5 从 1 → 4 条、CCTV9 从 1 → 4 条。
    # 用户要的"一个台多个源"卡在这里 —— 不是代码不给，是源本来就只有一两条。
    # 不挂 jsdelivr 镜像：同一份内容拉两遍只是浪费时间，央视候选数一条也多不了。
    ("https://raw.githubusercontent.com/suxuang/myIPTV/main/榴芒电视.txt", 1),
    ("https://raw.githubusercontent.com/suxuang/myIPTV/main/移动IPTV.m3u", 1),
    ("https://raw.githubusercontent.com/wwb521/live/main/tv.txt", 1),
    # 社区爬的，贴错标签比例高，只当最后手段
    ("https://iptv-org.github.io/iptv/countries/cn.m3u", 6),
]

# 编号模板源：(地址模板 % 台号, 适用台号)
#
# 运营商 CDN 里常见 `.../tsfile/live/0005_1.m3u8` 这种纯编号路径，编号就是台号。
# 上游列表只零星抄了 0008 / 0012 两条，于是 CCTV1/3/5/6/7/11 在"上游查无此源"，
# 用户那边表现就是"这几个台没画面" —— 其实源一直在，只是没人把地址写进列表。
#
# 2026-11-25 逐条实测：0001~0015 全部返回真 TS 切片（第一个字节 0x47），
# 0016 起 404。CCTV16 / CCTV5+ 不在这套编号里。
NUMBERED_SOURCES = [
    ("http://124.165.251.82:85/tsfile/live/%04d_1.m3u8?key=txiptv&playlive=1&authid=0",
     range(1, 16)),
    # 同构的第二台（同一套 IPTV 系统的另一个出口），编号同样等于台号。
    # 实测 5/6/8/9/10/12/13/15/17 可用，1/3 这两个编号当前返回 0 字节 ——
    # 照样全部生成，让测活去筛：可用的会排前面，坏的自己沉底。
    ("http://222.169.85.8:9901/tsfile/live/%04d_1.m3u8?key=txiptv&playlive=1&authid=0",
     range(1, 18)),
]

MAX_WORKERS = 24
# 每台留几个候选去测。这个数字现在直接决定"一个台能放几条源" —— 测完有几条能用
# 就全放进去，不设上限（影视仓会把同名的几条合并成一个台的多条播放源，列表不会变长）。
# 央视名额最多：免费源里它们最容易被 rtp 组播 / 境外灰产源占坑，多留几条才有一条能播；
# 卫视次之；其余给少一些，避免把测活时间全花在地方台上（CI 总共只有几十分钟）。
CAND_CCTV, CAND_WS, CAND_OTHER = 12, 8, 3
# 央视/卫视一个台最多写几条线路（写进 txt 的 `#` 队列）。上限 6 是因为实测候选池
# 就这么大：CCTV1 全网也就 5~7 条互不相同的地址，再多是从同一批列表里重复抄的。
MAX_LINES_MAIN = 6
# 终审（抓两轮节目单）每条要等十几秒，所以只验最靠前的几条：
# 央视/卫视验 5 条，其余 2 条
CHECK_CCTV, CHECK_OTHER = 5, 2
PLAYLIST_LIMIT = 512 * 1024
SEG_BYTES = 4096
STALE_SECONDS = 1800       # 切片超过 30 分钟没更新才算定格（太严会误杀打包源）
TRUST_OK = 3               # 可信度 <= 3 的上游：这次测不通也保留（CI 在境外，测不通≠源坏）
WEAK_TRUST = 6             # >= 这个可信度算"弱源"，只在前两级都拿不到时才用
# 通过校验的频道占比低于这个值只报警、不中止（写出去的都是验证过的，安全）
MIN_COVERAGE = float(os.environ.get("MIN_COVERAGE", "0.5"))
# 已知会"串台"或**根本连不上**的主机，填域名（子串匹配）即可永久拉黑。
#
# 下面这批是 2026-11-24 在用户实际网络（深圳）上逐个实测出来的：同一批主机在
# GitHub Actions（境外）里长期"没测通"，被当成"网络原因、家里能播"而当作未验证源
# 保留，结果用户点开一半的台都没画面。实测证明它们是真连不上（超时/拒绝），不是
# 跑测机的网络问题 —— "CI 测不通 ≠ 源坏"这条假设只对一部分源成立，对这批不成立。
#
# 光前 6 个主机就吃掉了 165 个台的位置（占当时输出的一半以上）。其中 huuc.edu.cn
# 是华北水利水电大学的校园镜像，只对教育网开放，任何家庭宽带都进不去 —— 但它
# 路径写成 /hls/cctv1hd.m3u8，台号自证满分，反而被排到了第一位，把能用的湖南广电
# 源挤掉了。这是"地址自证"权重的系统性陷阱：校园镜像和灰产站的命名都很规范
# （cctv1hd），正规广电 CDN 反而用内部编号（134_180adf）。
BLOCK_HOSTS = (
    "iptv.huuc.edu.cn",    # 校园镜像，只对教育网开放
    "t.061899.xyz",        # 一个脚本分发 33 个台，实测连不上
    "zby.130519.xyz",
    "101.35.240.114",
    "175.8.28.207",
    "148.135.93.213",
    "r.jdshipin.com",
    "php.jdshipin.com",
    "112.27.235.94",
    "8.138.7.223",
    "t.freetv.fun",
    "live.264788.xyz",
    "nn.7x9d.cn",
    "tvbox6.icu",          # 已知串台
    "p.ytelc.com",         # 已知串台（cctv.php?id=cctv4k 被标成 CCTV17）
    # 2026-11-25 用户实测：CCTV20 这一行播出来是 CCTV1 的画面。
    # 这是百度直播的用户流（stream_bduid_xxx），本质是某个人在转播 CCTV1，
    # 上游把这条个人直播贴上了"CCTV20"的标签。这类用户流随时换内容，不能要。
    "p2.bdstatic.com",
)

# 实测**能出真 TS 切片**的主机。
#
# 判定标准比"能不能连上"严一档：抓播放列表 → 取最后一个切片 → 下载前 512 字节，
# 第一个字节必须是 0x47（TS 同步字节）或含 ftyp（fMP4）。只看 #EXTM3U 是不够的 ——
# 有一大批服务器播放列表还在、切片已经全换成 479 字节的 HTML 错误页，连上去像好的，
# 播起来是黑的。
#
# 为什么需要这份名单：GitHub 的机器在境外，国内 IPTV 源大面积拒境外 IP，CI 根本
# 判断不了哪条能播。名单是照用户的实际网络测出来的，比 CI 的结论准。
#
# 2026-11-25 全量重测后重写：
#   ★ 保留 / 新增（实测切片为真 TS）
#       124.165.251.82:85   编号 CDN，1~15 + 19 全部真 TS，央视主力
#       222.169.85.8:9901   同构编号 CDN，5/6/8/9/10/12/13/15/17 可用
#       play.kankanlive.com 扛 CCTV2/4/9/10/11/12/14/15/17（CCTV1 那条边缘节点已废）
#       event.pull.hebtv.com / ali-m-l.cztv.com / 0472.org / idclive.hljtv.com
#       bp-resource-dfl.bestv.cn / english-livetx.cgtn.com / liveout.xntv.tv
#   ✗ 删除（11-24 晚还是好的，11-25 全部失效：播放列表 200、切片是 479B HTML）
#       198.204.228.26  192.151.150.154  47.97.252.137  74.91.26.218
#       38.75.136.137   63.141.230.178   120.76.248.139
#     这一批是同一运营者的多个入口（目录结构、命名、报错页完全一样），
#     74.91 根目录还挂着"服务器升级及付费服务公告" —— 试用期到了就集体失效。
#     它们的教训：**能连上 ≠ 能播**，而且这类"代理式"服务器随时会变。
#
# 维护方式：某个台一直没画面 → 把它的地址主机加进 BLOCK_HOSTS；发现新的能播主机
# → 加到这里（要先抓切片验过头 512 字节，别看 #EXTM3U 就加）。
PREFER_HOSTS = (
    "124.165.251.82",
    "222.169.85.8",
    "play.kankanlive.com",
    # 广电 / 运营商正规 CDN
    "event.pull.hebtv.com",
    "ali-m-l.cztv.com",
    "ali-xwl.cztv.com",
    "l.cztvcloud.com",
    "bp-resource-dfl.bestv.cn",
    "idclive.hljtv.com",
    "hlsal-ldvt.qing.mgtv.com",
    "m3u8.channel.wsrtv.com.cn",
    "119.39.9.8",
    "218.84.12.186",
    "221.7.175.154",
    "live.zohi.tv",
    "0472.org",
    "p2.bdstatic.com",
    # CGTN 官方
    "espanol-liveali.cgtn.com",
    "english-livetx.cgtn.com",
)

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_BRACKET = re.compile(r"[\[【(（][^\]】)）]*[\]】)）]")
_LINE_NO = re.compile(r"(线路|备用|源)\s*\d+")
_QUALITY = re.compile(
    r"(超高清|蓝光|超清|高清|标清|流畅|4K|8K|1080P|720P|540P|FHD|UHD|HD|SD|"
    r"H\.?265|HEVC|AVC|50FPS|60FPS|30FPS|25FPS)",
    re.I,
)
_CCTV_NUM = re.compile(r"cctv[\-_ ]?(\d{1,2})", re.I)
# 扫整条地址（含 query）里的 CCTV 台号。之前只扫 path，对
# `http://xxx/tv/migu.php?id=cctv5p` 这种把台号放在 query 里的地址等于没校验，
# 于是"标着 CCTV1、实际是 5+"的源被原样放行 —— 点 CCTV1 播出 5+ 就是这条路径。
_CCTV_TOK = re.compile(r"cctv[\-_ ]?(?:plus)?(\d{1,2})(?![0-9])", re.I)
# 运营商常见的 tsfile 编号：/tsfile/live/0003_1.m3u8 → 3
_TS_NUM = re.compile(r"(?:live|tsfile)[/_]0*(\d{1,2})_\d", re.I)


def url_ch_nums(url):
    """地址里所有能自证的 CCTV 台号（主号，5+ 与 5 都算 5）。"""
    u = unquote(url or "")
    return {int(m.group(1)) for m in _CCTV_TOK.finditer(u)}


def url_ch_hint(url, want):
    """这个地址有多能「自己证明」是第 want 台。分三档：

    2 = 路径里就写着这个台（/cctv1/、/tsfile/live/0001_1.m3u8）—— 每个台独立路径，
        串台概率最低；
    1 = 只有 query 里带台号（live.php?id=CCTV1）—— 一个脚本分发多个台，映射表错
        一条就是"点 CCTV1 播 5+"，可靠性差一档；
    0 = 完全看不出来。
    """
    u = unquote(url or "")
    path = u.split("?", 1)[0]
    if re.search(r"cctv[\-_ ]?(?:plus)?%d(?![0-9])" % want, path, re.I):
        return 2
    m = _TS_NUM.search(path)
    if m and int(m.group(1)) == want:
        return 2
    if re.search(r"cctv[\-_ ]?(?:plus)?%d(?![0-9])" % want, u, re.I):
        return 1
    return 0


def host_of(url):
    m = re.match(r"^https?://([^/?#]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


def is_blocked(url):
    h = host_of(url)
    return bool(h) and any(h == b or h.endswith("." + b) or b in h for b in BLOCK_HOSTS)


def is_preferred(url):
    """这条地址的主机是不是实测能连上的（见 PREFER_HOSTS）。"""
    h = host_of(url)
    return bool(h) and any(h == p or h.endswith("." + p) or p in h for p in PREFER_HOSTS)

_CN2CCTV = [
    ("体育赛事", "5+"),
    ("中文国际", "4"),
    ("国防军事", "7"),
    ("社会与法", "12"),
    ("农业农村", "17"),
    ("奥林匹克", "16"),
    ("综合", "1"),
    ("财经", "2"),
    ("综艺", "3"),
    ("体育", "5"),
    ("电影", "6"),
    ("电视剧", "8"),
    ("纪录", "9"),
    ("科教", "10"),
    ("戏曲", "11"),
    ("新闻", "13"),
    ("少儿", "14"),
    ("音乐", "15"),
]


def log(*args):
    print(*args, flush=True)


# ---------------------------------------------------------------- 频道名归一化

def canon(name):
    """把各种写法的频道名压成唯一标识：CCTV-3 / CCTV3 高清 / 中央电视台综艺频道 → CCTV3"""
    s = (name or "").strip()
    if not s:
        return ""
    # 4K/8K 要先去判，否则 "CCTV4K" 里的 4K 会被当作画质后缀删掉，退化成 "CCTV"
    raw = re.sub(r"\s+", "", s).upper()
    if "CCTV4K" in raw or re.match(r"^CCTV[\-_]?4K", raw):
        return "CCTV4K"
    if "CCTV8K" in raw or re.match(r"^CCTV[\-_]?8K", raw):
        return "CCTV8K"

    s = _BRACKET.sub("", s)
    s = _LINE_NO.sub("", s)
    s = _QUALITY.sub("", s)
    s = s.replace("－", "-").replace("—", "-").replace("　", "")
    s = re.sub(r"[\s\-_·•]+", "", s)
    if not s:
        return ""

    up = s.upper()
    m = re.match(r"^CCTV(\d{1,2})(\+|PLUS)?", up)
    if m:
        # CCTV-4欧洲 / CCTV-4美洲 是海外版，内容跟国内 CCTV4 不是一路，必须分开
        tail = up[m.end():]
        for k in ("欧洲", "美洲", "亚洲", "海外"):
            if k in tail:
                return "CCTV" + m.group(1) + k
        return "CCTV" + m.group(1) + ("+" if m.group(2) else "")

    # 只有明确是中央台的中文名才做映射，避免把地方"体育频道"也吞进来
    if "中央" in s or "CCTV" in up:
        for kw, num in _CN2CCTV:
            if kw in s:
                return "CCTV" + num

    if "卫视" in s:
        return re.sub(r"(电视台|频道)$", "", s)

    # CGTN 各语言台：不同列表写法不同（西班牙语/西语、阿拉伯语/阿语），不归一会让
    # 列表里并排出现两个台名，看着像重复台
    if up.startswith("CGTN") or "中国国际电视台" in s:
        for long_, short_ in (("西班牙", "西"), ("阿拉伯", "阿")):
            if long_ in up:
                return "CGTN" + short_ + "语"
        return re.sub(r"(外国语|外语)纪录", "纪录", up if up.startswith("CGTN") else s)

    return s


_CCTV_OK = re.compile(r"^CCTV\d{1,2}(\+|PLUS)?(欧洲|美洲|亚洲|海外)?$")
# "1995年""1996年""2024年" 这类是某些列表里的历年春晚/年播目录，不是频道
_JUNK_CH = re.compile(r"^((19|20)\d{2}|\d+$|测试|备用|直播源)")
_CCTV_CN = re.compile(r"^CCTV[\u4e00-\u9fa5][\u4e00-\u9fa5&·]{1,9}$")


def valid_ch(ch):
    """挡住假台名和垃圾条目：iptv-org 里有一堆 "CCTV+1"、"CCTVStormFootball"、
    "CCTVWomen'sFashion" 这类把境外台挂上 CCTV 前缀的条目；另外有些列表混进
    "1995年"、"1996年" 这种历年春晚目录，一条一个台，会把频道表撑到五百多个。"""
    if not ch or _JUNK_CH.match(ch):
        return False
    if not ch.startswith("CCTV"):
        # 纯英文名（HarbinComprehensiveNewsChannel、ABCNewsLive…）基本都是境外的
        # 小型转播站，既不是你要的台，又最容易出现"换台/广告"。清掉它们能把测活
        # 名额留给央视和卫视 —— 之前 1084 个频道里将近一半是这种东西。
        if not re.search(r"[\u4e00-\u9fa5]", ch):
            return False
        return True
    if ch in ("CCTV4K", "CCTV8K"):
        return True
    if _CCTV_OK.match(ch):
        return True
    return bool(_CCTV_CN.match(ch))      # 央视付费频道：CCTV怀旧剧场 / CCTV文化精品


def is_main_ch(ch):
    """央视 / CGTN / 卫视 —— 用户真正在看的台，值得给"未验证也保留"的待遇。"""
    return ch.startswith(("CCTV", "CGTN")) or "卫视" in ch


def group_of(ch):
    if ch.startswith("CCTV") or ch.startswith("CGTN"):
        return "央视频道"
    if "卫视" in ch:
        return "卫视频道"
    return "地方台"


def ch_order(ch):
    if ch in ("CCTV4K", "CCTV8K"):
        return (0, 90, 0)
    if ch.startswith("CGTN"):
        return (0, 95, 0)
    m = re.match(r"^CCTV(\d{1,2})(\+)?(.*)$", ch)
    if m:
        suffix = m.group(3) or ""
        # 同一台号内部顺序：本台 → 5+ 这类加大版 → 欧洲/美洲等海外版
        rank = 1 if m.group(2) else (2 if suffix else 0)
        return (0, int(m.group(1)), rank)
    if "卫视" in ch:
        return (1, 0, 0)
    return (2, 0, 0)


# ---------------------------------------------------------------- 拉取与解析

def fetch_text(url, timeout=(6, 15), retries=2):
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200 and r.text.strip():
                r.encoding = "utf-8"
                return r.text
            log(f"  拉取失败 {url} -> HTTP {r.status_code}")
        except Exception as e:
            log(f"  拉取失败 {url}: {e}")
        time.sleep(1 + i)
    return None


def clean_url(u):
    """清掉 TVBox txt 风格的尾巴。很多列表写成
        http://host/x.m3u8$LR•IPV4『线路138』
    `$` 后面是分组标记，不是地址的一部分；不切掉的话这条 URL 100% 打不开，
    而这类列表恰恰是央视 3~11 覆盖最全的那种。
    """
    u = (u or "").strip()
    for sep in ("$", "|"):
        if sep in u:
            u = u.split(sep, 1)[0]
    return u.strip()


def parse(text):
    """解析 m3u 或 TVBox 风格 TXT，统一成 [{name, tvg_id, logo, url}]

    两种格式都要认，因为上游列表里一半是 TXT：
        #EXTINF:-1 tvg-id="cctv1",CCTV1        ← m3u
        http://a/cctv1.m3u8
    和
        央视频道,#genre#                        ← TVBox txt
        CCTV1,http://a/cctv1.m3u8#http://b/...
    早先只认 m3u，导致上游里的 TXT 列表解析出 0 条、被当成"失效上游"丢掉 ——
    白白损失了上百条央视候选（CCTV1 的候选因此长期只有 2 条）。
    """
    out = []
    if "#EXTINF" in text:
        cur = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                attrs = dict(ATTR_RE.findall(line))
                tail = line.rsplit(",", 1)[-1].strip()
                name = (attrs.get("tvg-name") or tail or "").strip()
                cur = {
                    "name": name,
                    "tvg_id": (attrs.get("tvg-id") or "").strip(),
                    "logo": (attrs.get("tvg-logo") or "").strip(),
                }
            elif line.startswith("#"):
                continue
            elif cur is not None:
                cur["url"] = clean_url(line)
                out.append(cur)
                cur = None
        return out

    # TVBox 风格 TXT：每行 "频道名,地址"，一行里几条源用 # 连
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "#genre#" in line or line.startswith("#"):
            continue
        if "," not in line:
            continue
        name, urls = line.split(",", 1)
        name = name.strip()
        if not name:
            continue
        for u in urls.split("#"):
            u = clean_url(u.strip())
            if not u.lower().startswith(("http://", "https://")):
                continue
            out.append({"name": name, "tvg_id": "", "logo": "", "url": u})
    return out


# ---------------------------------------------------------------- 流校验

def _request(url, headers, timeout, stream=False):
    """正常请求；证书报错时退回不校验（不少地方台/运营商源用自签证书）"""
    try:
        return requests.get(url, headers=headers, timeout=timeout, stream=stream)
    except Exception as e:
        if type(e).__name__ != "SSLError":
            raise
        return requests.get(url, headers=headers, timeout=timeout, stream=stream, verify=False)


def _http_bytes(url, limit=PLAYLIST_LIMIT, timeout=(6, 10), extra=None):
    try:
        r = _request(url, {**HEADERS, **(extra or {})}, timeout, stream=True)
        try:
            if r.status_code >= 400:
                return None
            buf = b""
            for chunk in r.iter_content(4096):
                buf += chunk
                if len(buf) >= limit:
                    break
            return buf
        finally:
            r.close()
    except Exception:
        return None


def _seg_ok(seg_url):
    """最后一个切片能否真的下载（很多源节目单还在、切片已经 404）"""
    try:
        r = _request(
            seg_url,
            {**HEADERS, "Range": f"bytes=0-{SEG_BYTES - 1}"},
            (6, 8),
            stream=True,
        )
        try:
            if r.status_code not in (200, 206):
                return False, None
            first = next(r.iter_content(256), b"") or b""
            lm = r.headers.get("Last-Modified")
        finally:
            r.close()
        if len(first) < 100:
            return False, lm
        if first.lstrip()[:1] == b"<":      # 返回 HTML 错误页
            return False, lm
        return True, lm
    except Exception:
        return False, None


def _stale(last_modified):
    if not last_modified:
        return False
    try:
        ts = parsedate_to_datetime(last_modified).timestamp()
        return (time.time() - ts) > STALE_SECONDS
    except Exception:
        return False


def _media(url, text=None):
    if text is None:
        body = _http_bytes(url, limit=PLAYLIST_LIMIT)
        if not body:
            return None
        text = body.decode("utf-8", "ignore")
    up = text.upper()
    if "#EXTM3U" not in up or "#EXT-X-STREAM-INF" in up:
        return None
    segs = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    if not segs:
        return None
    ok, lm = _seg_ok(urljoin(url, segs[-1]))
    if not ok:
        return None
    if _stale(lm):
        return None
    return {"state": "endlist" if "#EXT-X-ENDLIST" in up else "live"}


def inspect(url):
    """返回 None（不可用）或 {'state': 'live'|'rawts'|'endlist'}"""
    if not url.lower().startswith(("http://", "https://")):
        return None
    body = _http_bytes(url, limit=PLAYLIST_LIMIT)
    if not body:
        return None
    if body[:1] == b"\x47" and len(body) > 188:      # 裸 TS，无法解析节目单
        return {"state": "rawts"}
    text = body.decode("utf-8", "ignore")
    if "#EXTM3U" not in text.upper():
        return None
    if "#EXT-X-STREAM-INF" in text.upper():
        base = url
        variants = [
            urljoin(base, l.strip())
            for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        for v in variants[:2]:
            r = _media(v)
            if r:
                return r
        return None
    return _media(url, text)


def score(entry):
    """返回 (档位, 档内细项)，数字越小越优先。

    第一层是"档位"。把优先级写成分档而不是一堆加减法，档与档之间的关系就是绝对的，
    不会再出现"某个加分项把未验证的源顶到已验证前面"这种事：

        0  可信上游 + 已确认在直播
        1  可信上游 + 裸 TS（解不出节目单，但地址通）
        2  可信上游 + 这次没测通      ← CI 在境外，连不上国内源是常态
        3  弱上游   + 已确认在直播
        4  弱上游   + 裸 TS
        9  死了 / 点播广告 / 定格

    为什么档 2 要排在档 3 前面：跑测机（GitHub Actions）在境外，能测通的往往只剩
    iptv-org 这类贴错标签率极高的源，而那正是"点 CCTV3 出来是卖药广告"的来源。
    宁可给一条没验证过的国内可信源，也不给一条验证过但很可能是广告的弱源。

    第二层是档内怎么排：
      - 上游可信度：数字越小越优先
      - 台号自证：地址里写着 cctv1 / 0001 的往前（路径自证 -8 > 仅 query 自证 -4 >
        看不出 0）。这是"点 CCTV1 播出 5+"唯一可编程的判据
      - 其余并列时保持上游顺序（Python 的 sort 是稳定的），也就是 suxuang 这类
        国内列表里的地址优先 —— 这是实测出来的结论，见下面这段

    但有一档凌驾于以上所有：**PREFER_HOSTS 里的主机**（实测能连上的）直接返回
    档位 -1。因为跑测机在境外，它眼里的"已验证"和用户家里能不能播基本无关，
    而这份名单是照着用户的实际网络测出来的，比任何推断都准。

    2026-11-24 实测修正：这一层曾经加过"上游共识"（同一条地址被越多列表收录越
    优先，-2/票最多 -6），结果用户反馈"CCTV 大部分台都看不了，只有 CCTV5+、CCTV9
    少数几个能看，前两天 CCTV1/2/3 都是可以看的"。原因是：免费列表之间大量互相
    抄袭，抄得最多的恰恰是同一个野鸡转播地址；真正能播的运营商源往往只有一个
    国内列表收录。共识越高 = 被抄得越多，不等于越能播。已删除该权重。
    """
    trust = int(entry.get("trust", 9))
    trusted = trust <= TRUST_OK
    st = entry.get("state")

    sub = trust * 2
    sub -= 4 * int(entry.get("ch_hint") or 0)
    if not entry["url"].lower().startswith("https"):
        sub += 1

    # 2026-11-25 改：优先级从"主机可不可信"改成"这条到底能不能播"。
    #
    # 之前的写法是 `if is_preferred: return (-1, sub)` —— PREFER 主机上的源无条件
    # 排第一，哪怕它这次测下来是死的。用户实测的结果就是：CCTV1 第一条开不出画面，
    # 得一直往下切。而 PREFER 名单再准，也只是"主机活着"，不等于"这条流是好的"
    # （198.204.228.26 的播放列表还在、切片已经全变成 479 字节的错误页）。
    #
    # 现在分三轮：
    #   ① 切片验证通过（live）—— 不管上游可不可信，能播就是能播
    #   ② 裸 TS（rawts）—— 解不出节目单，但地址确实在吐数据
    #   ③ 没验证过（unverified）—— 只有 PREFER 主机 / 可信上游的才留
    # 同一轮里，PREFER 主机排在前面（它是照着用户实际网络测出来的）。
    pref = is_preferred(entry["url"])

    # 编号模板源（在这台机器上验过切片是真 TS）：永远排最前。
    # CI 在境外测不通它们，但那不代表用户家里播不了 —— 反而是反过来才对。
    # tpl 小的优先（124.165 全编号都好，排在只有部分编号好的 222.169 之前）。
    if entry.get("local_ok"):
        return (-2, int(entry.get("tpl") or 0) * 100 + sub)

    if st == "live":
        tier = -1 if pref else (0 if trusted else 1)
    elif st == "rawts":
        tier = 2 if pref else (3 if trusted else 4)
    elif st == "unverified":
        tier = 5 if pref else (6 if trusted else 9)
    elif st is None:
        # 裁剪候选时还没测活，state 是空的。这时候也得让 PREFER 主机排在前面，
        # 否则 CAND_CCTV 只留 12 条，13 条模板源加一堆弱源一挤，PREFER 的源反被裁掉。
        tier = 7 if pref else (8 if trusted else 9)
    else:                                  # endlist / frozen / dead
        tier = 9
    return (tier, sub)


# ---------------------------------------------------------------- 终审：确认"真的在直播"
#
# 前面的 inspect() 只能证明"这个地址能取到节目单、切片下载得下来"。广告源、电影循环源、
# 台标贴错的源都能通过。真正能拉开差距的一条判据是：直播流的切片必须一直在往前滚。
# 所以这里间隔十几秒抓两次节目单做比对 —— 不需要解码画面，跑得完，也够狠。

def _playlist_text(url):
    body = _http_bytes(url, limit=PLAYLIST_LIMIT)
    if not body:
        return None
    text = body.decode("utf-8", "ignore")
    if "#EXTM3U" not in text.upper():
        return None
    return text


def _resolve(url, text=None, depth=0):
    """master 节目单 → 一条真正带切片的子流；(media_url, text) 或 (None, None)"""
    t = text if text is not None else _playlist_text(url)
    if not t:
        return None, None
    if "#EXT-X-STREAM-INF" not in t.upper():
        return url, t
    if depth > 1:
        return None, None
    variants = [
        urljoin(url, l.strip())
        for l in t.splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]
    for v in variants[:2]:
        mu, mt = _resolve(v, None, depth + 1)
        if mu:
            return mu, mt
    return None, None


def _snapshot(text, base):
    """(media-sequence, 最后一个切片, targetduration, 是否带 ENDLIST)"""
    up = text.upper()
    m = re.search(r"#EXT-X-MEDIA-SEQUENCE:\s*(\d+)", up)
    seq = int(m.group(1)) if m else None
    m = re.search(r"#EXT-X-TARGETDURATION:\s*(\d+)", up)
    tdur = int(m.group(1)) if m else 6
    segs = [l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")]
    last = urljoin(base, segs[-1]) if segs else ""
    return seq, last, tdur, ("#EXT-X-ENDLIST" in up)


def _poll(urls, workers):
    """抓一轮基线：{url: (媒体节目单地址, 快照)}"""
    def one(u):
        mu, mt = _resolve(u)
        if not mu:
            return u, None
        return u, (mu, _snapshot(mt, mu))

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for u, r in ex.map(one, urls):
            out[u] = r
    return out


def _compare(bases, workers):
    """再抓一轮和基线比：{(url): (是否在直播, 原因)}"""
    def one(item):
        u, r = item
        if not r:
            return u, (False, "nofetch")
        mu, snap0 = r
        t = _playlist_text(mu)
        if not t:
            return u, (False, "nofetch")
        seq1, last1, _, end1 = _snapshot(t, mu)
        seq0, last0, _, end0 = snap0
        if end0 or end1:                      # 已经结束的点播文件 = 广告 / 电影循环
            return u, (False, "endlist")
        if seq0 is not None and seq1 is not None and seq1 != seq0:
            return u, (True, "seq")
        if last1 and last1 != last0:
            return u, (True, "seg")
        return u, (False, "static")

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for u, r in ex.map(one, bases.items()):
            out[u] = r
    return out


def confirm_many(urls, workers=MAX_WORKERS):
    """间隔若干秒抓两次节目单：真直播的切片必然往前滚；广告 / 电影循环源要么带
    ENDLIST，要么序列不动，两者都判死。落榜的再复核一轮，避免 CDN 缓存了节目单
    造成误杀（不少 IPTV 中转服务器的 m3u8 有 10~30 秒缓存）。

    已知边界：它证明不了"画面就是这个台"。一条 24 小时卖药的直播流同样会通过，
    这种只能靠上游可信度排序去压，代码层面验不出来。
    """
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return {}

    bases = _poll(urls, workers)
    tdurs = [r[1][2] for r in bases.values() if r]
    wait = max(8, min(14, (max(tdurs) if tdurs else 6) + 3))
    log(f"[终审] 间隔 {wait}s 抓两轮节目单，确认是否真在直播（{len(urls)} 条）…")
    time.sleep(wait)
    res = _compare(bases, workers)

    retry = [u for u, (ok, why) in res.items() if not ok and why != "endlist"]
    if retry:
        log(f"[终审] {len(retry)} 条这轮没看出在动，再等一轮复核（排除节目单被缓存）…")
        b2 = _poll(retry, workers)
        time.sleep(wait)
        for u, (ok, _why) in _compare(b2, workers).items():
            if ok:
                res[u] = (True, "retry")

    passed = sum(1 for ok, _ in res.values() if ok)
    log(f"[终审] 确认在直播 {passed} / {len(res)} 条")
    return {u: ok for u, (ok, _) in res.items()}


# ---------------------------------------------------------------- 主流程

def main():
    log("[启动] live.py")

    entries = []
    seen_up = set()
    dead_up, suspect = [], []
    for up, trust in UPSTREAMS:
        if up in seen_up:
            continue
        seen_up.add(up)
        log(f"[拉取] {up}")
        txt = fetch_text(up)
        if not txt:
            dead_up.append(up)
            continue
        got = parse(txt)
        n_cctv = sum(1 for it in got if canon(it["name"]).startswith("CCTV"))
        log(f"  √ 解析 {len(got)} 条（含央视 {n_cctv} 条，可信度 {trust}）")
        # 一个正常的国内列表不可能一条央视都没有 —— 出现了就说明这个地址已经失效，
        # 只是刚好返回了别的内容（这是上一版央视整体消失的直接原因，必须吵出来）
        if len(got) < 10 or n_cctv == 0:
            log("  [上游异常] 内容不像完整的频道表（条数过少或没有央视），权重按最差处理")
            trust = 9
            suspect.append(up)
        for it in got:
            it["trust"] = trust
            entries.append(it)
    log(f"[原始] 共 {len(entries)} 条")

    if dead_up or suspect:
        log(f"[上游检查] 抓取失败 {len(dead_up)} 个 / 内容异常 {len(suspect)} 个")
        for u in dead_up:
            log(f"    失效  {u}")
        for u in suspect:
            log(f"    异常  {u}")

    # 0.9) 编号模板补源。
    #
    # 有些运营商 CDN 直接用 4 位编号对应台号（0001=CCTV1 … 0015=CCTV15），但上游
    # 列表里只零星收了 0008 / 0012 两条 —— 恰好就是用户说"能播"的那两个台。
    # 其余台不是没有源，是没人把地址抄进列表（维护者只抄别人抄过的）。
    #
    # 2026-11-25 在这台机器上逐条实测：0001~0015 全部返回真 TS 切片（0016 起 404）。
    # 所以按台号直接生成候选，让 CCTV1/3/5/6/7/11 这些"上游查无此源"的台有确定
    # 能播的地址。这些源照样走后面的测活 / 排序流程，不搞特殊待遇 —— 万一哪天
    # CDN 挂了，它们会和别的死源一样沉到线路末尾。
    for ti, (tpl, nums) in enumerate(NUMBERED_SOURCES):
        for n in nums:
            entries.append({
                "name": f"CCTV{n}", "tvg_id": "", "logo": "",
                "url": tpl % n, "trust": 1,
                # local_ok：在这台机器上抓过切片、确认真 TS。CI 在境外，它测不通
                # 这些国内 CDN 很正常 —— 但不能因此让它们沉到线路末尾，
                # 否则用户点开第一条就是黑的（实测就有过一次）。
                "local_ok": True, "tpl": ti,
            })

    # 1) 归一化频道名
    for it in entries:
        it["ch"] = canon(it["name"])
    entries = [e for e in entries if e.get("ch") and e.get("url")]

    # 1.5) rtp/udp/rtsp 组播地址影视仓根本播不了（YueChan 的 CCTV3~17 全是这种），
    #      留着只会白占测活名额，让真正能播的候选轮不上
    n0 = len(entries)
    entries = [e for e in entries if e["url"].lower().startswith(("http://", "https://"))]
    if n0 != len(entries):
        log(f"[协议] 丢弃 {n0 - len(entries)} 条非 http 地址（rtp/udp 组播，影视仓播不了）")

    if BLOCK_HOSTS:
        n1 = len(entries)
        entries = [e for e in entries if not is_blocked(e["url"])]
        if n1 != len(entries):
            log(f"[拉黑] 丢弃 {n1 - len(entries)} 条来自 BLOCK_HOSTS 的地址")

    before = len(entries)
    entries = [e for e in entries if valid_ch(e["ch"])]
    if before != len(entries):
        log(f"[垃圾条目] 丢弃 {before - len(entries)} 条假台名 / 年份目录 / 测试条目")
    log(f"[归一化后] 共 {len(entries)} 条")

    # 2) 交叉校验：同一地址被不同上游标成不同频道 → 至少有一个是错的。
    #    但不能一刀切全丢：`migu.php?id=cctv5p` 被同时标成 CCTV1 和 CCTV5+ 时，
    #    全丢会把 CCTV5+ 唯一正确的源也删掉。所以先让地址自己作证（地址里的台号）：
    #    能和台号对上且只有一个认领者 → 归它；对不上或多人认领 → 整条丢弃。
    by_url = {}
    for e in entries:
        by_url.setdefault(e["url"], []).append(e)

    conflict = {u for u, lst in by_url.items() if len({e["ch"] for e in lst}) > 1}
    if conflict:
        winner, dropped = {}, 0
        for u in conflict:
            nums = url_ch_nums(u)
            match = []
            for e in by_url[u]:
                m = re.match(r"^CCTV(\d{1,2})", e["ch"])
                if nums and m and int(m.group(1)) in nums:
                    match.append(e["ch"])
            if len(set(match)) == 1:
                winner[u] = match[0]
        kept = []
        for e in entries:
            u = e["url"]
            if u in conflict:
                if winner.get(u) != e["ch"]:
                    dropped += 1
                    continue
            kept.append(e)
        entries = kept
        log(f"[标签冲突] {len(conflict)} 个地址被贴上多个台名："
            f"{len(winner)} 个按地址内台号归位，丢弃 {dropped} 条错标")

    # 3) 地址里的台号与标签不符 → 丢弃。这是"点 CCTV1 播出 CCTV5+"的直接防线：
    #    台号可能写在 path（/cctv1/index.m3u8、/tsfile/live/0001_1.m3u8），
    #    也可能写在 query（migu.php?id=cctv5p）。只扫 path 会漏掉后者。
    drop = 0
    kept = []
    samples = []
    for e in entries:
        m_ch = re.match(r"^CCTV(\d{1,2})", e["ch"])
        if not m_ch:
            kept.append(e)
            continue
        want = int(m_ch.group(1))
        got = url_ch_nums(e["url"])
        if got and want not in got:
            drop += 1
            if len(samples) < 3:
                samples.append(f"{e['ch']} ← {e['url'][:70]}")
            continue
        # 台号能自证（地址里就写着这个台）→ 排序时优先，避免被一条看不出台号的地址占坑
        lvl = url_ch_hint(e["url"], want)
        if not lvl and want in got:
            lvl = 1
        e["ch_hint"] = lvl
        kept.append(e)
    entries = kept
    if drop:
        log(f"[台号不符] 丢弃 {drop} 个地址内台号与名称不一致的源")
        for s in samples:
            log(f"    {s}")

    # 4) 按频道聚合并裁剪候选。候选数分级：央视给最多名额，因为免费源里它们
    #    最容易被 rtp 组播和境外灰产源占坑，得多试几条才有一条真的
    by_ch = {}
    for e in entries:
        d = by_ch.setdefault(e["ch"], {"name": e["ch"], "logo": "", "cands": []})
        if e.get("logo") and not d["logo"]:
            d["logo"] = e["logo"]
        if e["url"] not in [c["url"] for c in d["cands"]]:
            d["cands"].append(e)

    for ch, d in by_ch.items():
        d["cands"].sort(key=score)
        if ch.startswith(("CCTV", "CGTN")):
            n = CAND_CCTV
        elif "卫视" in ch:
            n = CAND_WS
        else:
            n = CAND_OTHER
        d["cands"] = d["cands"][:n]

    log(f"[频道] 归并后 {len(by_ch)} 个，待测候选 {sum(len(d['cands']) for d in by_ch.values())} 条")

    # 5) 并发测活（只测候选，不测全网）
    tasks = [(d, c) for d in by_ch.values() for c in d["cands"]]
    log(f"[候选] {len(tasks)} 条待测")
    if tasks:
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(inspect, c["url"]): (d, c) for d, c in tasks}
            for f in as_completed(futs):
                d, c = futs[f]
                done += 1
                if done % 100 == 0:
                    log(f"  测活 {done}/{len(tasks)}")
                try:
                    res = f.result()
                except Exception:
                    res = None
                if res:
                    c["state"] = res["state"]
                elif is_main_ch(c["ch"]) and (
                    int(c.get("trust", 9)) <= TRUST_OK or is_preferred(c["url"])
                ):
                    # 跑测机网络到不了 ≠ 源死了。可信列表里的源留着，排在验证过的后面。
                    # PREFER_HOSTS 里的源（用户网络实测可达，多半来自弱上游）同样留 ——
                    # CI 在境外测不通它，但用户家里能播，这正是要保住的那部分。
                    # 只对央视/卫视开这个口子：地方台数量上千，全放进来会有大量打不开的地址
                    c["state"] = "unverified"
                else:
                    c["state"] = "dead"

    # 6) 终审：只验"能取到节目单"的候选，央视/卫视多验几条
    to_check = []
    for ch, d in by_ch.items():
        vc = [c for c in d["cands"] if c["state"] == "live"]
        n = CHECK_CCTV if is_main_ch(ch) else CHECK_OTHER
        to_check.extend([c["url"] for c in sorted(vc, key=score)[:n]])
    ok_map = confirm_many(to_check)

    # 终审结论回写：序列不动的（点播广告 / 定格）单独标 frozen，和"没测到"区分开
    for d in by_ch.values():
        for c in d["cands"]:
            if c["url"] in ok_map and c["state"] == "live" and not ok_map[c["url"]]:
                c["state"] = "frozen"

    # 7) 选线：每台按档位排序后取前几条，线路之间尽量不同主机
    #    ① 可信上游 + 已验证在直播
    #    ② 可信上游 + 这次测不到（CI 在境外，家里大概率能播）
    #    ③ 弱上游 + 已验证     ← 只在前两级都没有时才用，日志点名
    #    弱上游 + 测不到 → 直接扔（既不准又不知道活没活）
    out, empty, weak, unver, doubt = [], [], [], [], []
    multi = 0
    for ch, d in by_ch.items():
        # 央视/卫视：进线路的三种情况
        #   ① 这次切片验证通过（live / rawts）—— 真能播，无条件进
        #   ② 没验证过，但主机在 PREFER_HOSTS 里 —— 那是照着用户实际网络测出来的，
        #      CI 在境外测不通它很正常，家里能播
        #   ③ 其余 unverified 不要。上一版"全放"的后果用户已经实测过了：
        #      CCTV1 的 6 条线路里 5 条开不出画面（那些主机的播放列表还在、切片
        #      已经全是 479 字节的错误页），得一条条往下试。
        # 用户的原话是"只要测活的地址就加到台里"，所以这里按"测活"收口，不按"凑数量"。
        if is_main_ch(ch):
            cands = [
                c for c in d["cands"]
                if c["state"] in ("live", "rawts")
                or (c["state"] == "unverified" and is_preferred(c["url"]))
            ]
            cands = cands[:MAX_LINES_MAIN]
        else:
            # 地方台上千个，只给确认能播的：否则一个台挂 5 个死地址，列表几万行。
            cands = [c for c in d["cands"] if c["state"] in ("live", "rawts")]
        cands.sort(key=score)
        # 同一地址只留一次（不同上游会把同一条地址写两遍）
        _seen, _uniq = set(), []
        for c in cands:
            if c["url"] in _seen:
                continue
            _seen.add(c["url"])
            _uniq.append(c)
        cands = _uniq
        if not cands:
            empty.append(ch)
            continue
        best = cands[0]
        trust = int(best.get("trust", 9))
        if trust >= WEAK_TRUST:
            weak.append(ch)
        if best["state"] == "unverified":
            unver.append(ch)
            # 「未验证」意味着这条源连内容都没被确认过，唯一的质量信号就是地址里
            # 有没有写明台号。CCTV 却完全看不出台号的地址，正是"点 CCTV1 播 5+"
            # 最容易发生的一类，单独吵出来，方便直接拉黑它的主机。
            if ch.startswith(("CCTV", "CGTN")) and not best.get("ch_hint"):
                doubt.append((ch, best["url"]))

        # 候选池已经在上面的过滤器里筛过了，这里全部写出 —— 会写成 TXT 的
        # `CCTV1,地址1#地址2#地址3`，影视仓读成"一个台 + 3 条线路"，列表不会变长。
        picked = cands
        if len(picked) > 1:
            multi += 1
        out.append((ch, d["name"], d["logo"], picked))

    final = out
    coverage = len(final) / max(1, len(by_ch))
    log(f"[成片率] {coverage:.0%}（{len(final)}/{len(by_ch)} 个频道）")
    if weak:
        log(f"[弱源] {len(weak)} 个台用的是低可信上游（贴错标签风险高）："
            + ", ".join(sorted(weak)[:24]) + (" ..." if len(weak) > 24 else ""))
    if unver:
        n_cctv = sum(1 for c in unver if c.startswith(("CCTV", "CGTN")))
        n_ws = sum(1 for c in unver if "卫视" in c)
        log(f"[未验证] {len(unver)} 个台的地址这次没测通，取自可信列表（家里通常能播）："
            + ", ".join(sorted(unver)[:24]) + (" ..." if len(unver) > 24 else ""))
        log(f"         其中央视/CGTN {n_cctv} 个、卫视 {n_ws} 个。这些台的内容本次没有"
            "任何一条被确认过，")
        log("         要么播得通、要么点开没反应或串台 —— 点开发现是别的台，告诉我台名，"
            "我把那条主机拉黑。")
    if doubt:
        log(f"[存疑] {len(doubt)} 个央视台没有能自证台号的源（地址里看不出是哪一台）：")
        for ch, u in doubt[:8]:
            log(f"    {ch}  {u[:78]}")
        log("         这类地址最容易串台。点开发现是别的台，把上面地址的主机名写进")
        log("         BLOCK_HOSTS 即可永久拉黑（或直接告诉我，我加进去）。")
    if multi:
        log(f"[多源] {multi} 个台带了 2 条以上播放源（写在同一行，用 # 连起来）：")
        log("       列表里仍然只显示一个台名；播放时按菜单键进「线路选择」就是")
        log("       源1 / 源2 / 源3，可以自己点着切，不用等它超时。")

    # 单入口脚本（同一 host+path，靠 ?id= 分发所有台）是串台高发结构：一个脚本按
    # 参数查表映射到不同频道，表里错一条就是"点 CCTV1 播 5+"。列出来便于逐个点验。
    grp = {}
    for ch, _n, _l, picked in final:
        k = picked[0]["url"].split("?")[0]
        grp.setdefault(k, set()).add(ch)
    hot = {k: v for k, v in grp.items() if len(v) >= 3}
    if hot:
        log(f"[批量源] {len(hot)} 个入口一个地址分发多个台（串台高发，建议点验）：")
        for k, v in sorted(hot.items(), key=lambda kv: -len(kv[1]))[:5]:
            log(f"    {k[:64]} → {len(v)} 个台：{', '.join(sorted(v)[:8])}")
        log("         点开发现串台，就把这个主机写进 BLOCK_HOSTS。")

    # 写出去的每一条都是"确认在直播"的，所以成片率低只报警、不中止：
    # 少几个台，总比多几个广告台好。成片率 0 才说明这次网络完全不通，保留旧文件。
    if by_ch and coverage < MIN_COVERAGE:
        log("=" * 64)
        log(f"[注意] 只有 {coverage:.0%} 的频道通过终审。基本都是当前网络到不了这些源，")
        log("       不是源死了。本次仍会写文件，但只写通过终审的那些。")
        log("       想让未通过的台也出现，只能在国内宽带的机器上重跑：python scripts/live.py")
        log("=" * 64)

    log(f"[可用频道] {len(final)} 个   [无可用源] {len(empty)} 个")
    if empty:
        log("  无源频道: " + ", ".join(sorted(empty)[:40]) + (" ..." if len(empty) > 40 else ""))

    if not final:
        if os.path.exists(OUTPUT):
            log(f"[兜底] 本次 0 个可用频道，保留上次 {os.path.basename(OUTPUT)}，不覆盖")
            log("       注意：这条路径说明本次一条都没验证成功（网络不通），旧文件里的台")
            log("       未必干净。请在能正常播放的宽带下跑一次 python scripts/live.py。")
        else:
            log("[致命] 无可用源且无历史文件")
        return 3

    # 8) 排序 + 输出（两个文件一起写，内容来自同一份 final）
    #
    #   live.txt —— 主输出，TVBox / 影视仓 风格：**同一个台名写多行，每行一个地址**
    #
    #       央视频道,#genre#
    #       CCTV1,http://a
    #       CCTV1,http://b
    #       CCTV1,http://c
    #
    #       为什么不用 `CCTV1,http://a#http://b`（第一次的做法）：影视仓这个版本
    #       不认 `#` 分隔符 —— 用户实测写了 6 条，线路选择里还是只有「源1」。
    #       原因是 `#` 在 URL 规范里是 fragment，播放器请求时会把它后面的整段丢掉，
    #       所以永远只用得到第一条。而 txt 解析器遇到同名频道会复用同一个 Channel
    #       对象、把地址 addUrl 进去，于是"同名多行"才是多线路的正确写法。
    #
    #   live.m3u —— 兼容老的直播地址，每台只写第一条源（带台标、分组）。
    #       不写多条：m3u 里同名的多条 #EXTINF 影视仓不会合并，会当成多个频道
    #       并列显示，列表就成了 2 CCTV2 / 3 CCTV2 / 4 CCTV3 / 5 CCTV3 ...
    GROUP_ORDER = {"央视频道": 0, "卫视频道": 1, "地方台": 2}
    final.sort(key=lambda x: (GROUP_ORDER.get(group_of(x[0]), 9), ch_order(x[0]), x[1]))

    txt, cur_g = [], None
    for ch, name, logo, picked in final:
        g = group_of(ch)
        if g != cur_g:
            if txt:
                txt.append("")            # 分组之间空一行，肉眼看也清楚
            txt.append(f"{g},#genre#")
            cur_g = g
        # dict.fromkeys 去重但保序：第一条永远是最该先试的那条
        for u in dict.fromkeys(c["url"] for c in picked):
            txt.append(f"{name},{u}")

    m3u = ["#EXTM3U"]
    for ch, name, logo, picked in final:
        attrs = f'tvg-id="{ch}" tvg-name="{name}" group-title="{group_of(ch)}"'
        if logo:
            attrs += f' tvg-logo="{logo}"'
        m3u.append(f"#EXTINF:-1 {attrs},{name}")
        m3u.append(picked[0]["url"])

    def _write(path, bak, lines):
        if os.path.exists(path):
            try:
                os.replace(path, bak)
            except Exception:
                pass
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    _write(OUTPUT, BACKUP, txt)
    _write(OUTPUT_M3U, OUTPUT_M3U + ".bak", m3u)

    stats = {}
    for ch, _, _, _ in final:
        g = group_of(ch)
        stats[g] = stats.get(g, 0) + 1
    log(f"[分组统计] {stats}")
    cctv_cnt = sum(1 for x in final if x[0].startswith(("CCTV", "CGTN")))
    if cctv_cnt == 0:
        log("[严重] 本次一个中央台都没生成 —— 上游列表大概率全部失效，请看上面 [上游失效] 那几行")
    n_urls = sum(len(list(dict.fromkeys(c["url"] for c in p))) for _, _, _, p in final)
    log(f"[完成] 频道 {len(final)} 个（央视 {cctv_cnt}）/ 播放源 {n_urls} 条 / "
        f"其中 {multi} 个台有多条可选")
    log(f"[输出] {OUTPUT}   ← 直播地址填这个（一个台一行）")
    log(f"       {OUTPUT_M3U}   ← 兼容老地址，每台只留第一条源")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
