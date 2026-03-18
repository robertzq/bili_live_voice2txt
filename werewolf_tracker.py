import re
import os
import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

class Player:
    def __init__(self, pid, initial_prob):
        self.pid = pid
        self.claim = ""         
        self.wolf_prob = initial_prob   # 动态概率
        self.is_dead = False
        self.death_type = ""            # x:白天抗推, xn:夜间倒牌
        self.real_role = ""     

class WerewolfEngine:
    def __init__(self, player_count=12, wolf_count=4):
        self.player_count = player_count
        self.wolf_count = wolf_count
        self.base_prob = round((wolf_count / player_count) * 100, 1)
        
        self.players = {i: Player(i, self.base_prob) for i in range(1, player_count + 1)}
        
        self.logs = []          
        self.raw_logs = []      
        self.history = set()    
        
        # 核心：关系图谱池。记录 (发起者, 目标, 动作类型)
        self.relations = []
        
        # ================= 终极语法糖字典 =================
        self.rules = {
            "claim": re.compile(r"^(\d+)=([a-zA-Z]+)$"),     
            "good": re.compile(r"^(\d+)\+(\d+)$"),           # 铁金水
            "bad": re.compile(r"^(\d+)\-(\d+)$"),            # 铁查杀
            "vouch": re.compile(r"^(\d+)\*(\d+)$"),          # 软保人
            "suspect": re.compile(r"^(\d+)\~(\d+)$"),        # 软怀疑
            "silver": re.compile(r"^(\d+)\@(\d+)$"),         # 银水
            "vote": re.compile(r"^([\d,]+)>(\d+)$"),         # 投票
            "dead": re.compile(r"^(\d+)x([nN]?)$"),          # 死亡
            "confirm": re.compile(r"^(\d+)!([a-zA-Z]+)$")    # 确认底牌
        }

    def add_log(self, ui_text, raw_text):
        self.logs.append(ui_text)
        self.raw_logs.append(raw_text)

    # ================= 图谱推演引擎 (权重大修版) =================
    def _apply_odds(self, player, ratio):
        """底层的赔率计算器"""
        p = player.wolf_prob / 100.0
        if p >= 0.999 or p <= 0.001: 
            return
        odds = p / (1.0 - p)
        new_odds = odds * ratio
        new_p = new_odds / (1.0 + new_odds)
        player.wolf_prob = new_p * 100.0

    def recalculate_all(self):
        """核心：信任链全局重算。支持暗牌模式与全神职动态互斥"""
        for p in self.players.values():
            if p.real_role:
                p.wolf_prob = 100.0 if p.real_role == 'W' else 0.0
            else:
                p.wolf_prob = self.base_prob

        for _ in range(3):
            # --- [逻辑A]：全神职动态对跳互斥 ---
            special_claims = set(p.claim for p in self.players.values() if p.claim and p.claim != 'V')
            
            for role in special_claims:
                claimers = [p for p in self.players.values() if p.claim == role]
                if len(claimers) > 1:
                    good_claimers = [p for p in claimers if p.real_role and p.real_role != 'W']
                    if good_claimers: 
                        for c in claimers:
                            if not c.real_role: self._apply_odds(c, 20.0)
                    else:
                        for c in claimers:
                            if not c.real_role: self._apply_odds(c, 1.5)

            # --- [逻辑B]：图谱连坐与倒推 ---
            for (src_id, tgt_id, act_type) in self.relations:
                src = self.players[src_id]
                tgt = self.players[tgt_id]
                
                p_src_w, p_src_g = src.wolf_prob / 100.0, 1.0 - (src.wolf_prob / 100.0)
                p_tgt_w, p_tgt_g = tgt.wolf_prob / 100.0, 1.0 - (tgt.wolf_prob / 100.0)

                # 1. 铁查杀 (极高权重)
                if act_type == 'bad':
                    if not tgt.real_role:
                        self._apply_odds(tgt, p_src_g * 5.0 + p_src_w * 0.2)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_g * 5.0 + p_tgt_w * 0.5)

                # 2. 铁金水 (极高权重)
                elif act_type == 'good':
                    if not tgt.real_role:
                        self._apply_odds(tgt, p_src_g * 0.2 + p_src_w * 3.0)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_g * 0.8 + p_tgt_w * 5.0)

                # 3. 软怀疑/踩 (温和权重)
                elif act_type == 'suspect':
                    if not tgt.real_role:
                        self._apply_odds(tgt, p_src_g * 1.3 + p_src_w * 0.8)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_g * 1.3 + p_tgt_w * 0.8)

                # 4. 软保人/站边 (抓倒钩专属权重)
                elif act_type == 'vouch':
                    if not tgt.real_role:
                        self._apply_odds(tgt, p_src_g * 0.7 + p_src_w * 1.5)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_w * 2.5 + p_tgt_g * 0.8)

                # 5. 银水防自刀
                elif act_type == 'silver':
                    if not tgt.real_role:
                        self._apply_odds(tgt, p_src_g * 0.3 + p_src_w * 2.0)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_w * 2.5 + p_tgt_g * 0.5)

                # 6. 投票连坐
                elif act_type == 'vote':
                    if not tgt.real_role:
                        self._apply_odds(tgt, p_src_g * 1.5 + p_src_w * 0.6)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_g * 1.5 + p_tgt_w * 0.5)

        # 3. [终极高阶逻辑]：双金水定理 & 残局防倒钩反弹
        alive_players = [p for p in self.players.values() if not p.is_dead]
        
        for p in self.players.values():
            if p.real_role: continue
            
            # 检测真正的双金水 (只有声称是 S 的人发的才算)
            good_sources = [src for src, tgt, act in self.relations if act == 'good' and tgt == p.pid]
            seer_sources = [src for src in good_sources if self.players[src].claim == 'S']
            
            if len(set(seer_sources)) >= 2:
                p.wolf_prob = 1.0 
                p.claim = f"{p.claim}(双金水)" if p.claim and "(双金水)" not in p.claim else p.claim or "(双金水)" 
                continue

            # 检测：防倒钩概率反弹
            if len(alive_players) <= 4 and p.wolf_prob < 15.0 and not p.is_dead:
                voted_for_dead = any(tgt for src, tgt, act in self.relations if src == p.pid and act == 'vote' and self.players[tgt].is_dead)
                if voted_for_dead:
                    p.wolf_prob = max(p.wolf_prob, 30.0)
                    p.claim = f"{p.claim}[⚠️疑倒钩]" if p.claim and "[⚠️疑倒钩]" not in p.claim else p.claim or "[⚠️疑倒钩]"

        # 4. 全局归一化
        self.normalize_probabilities()

    def get_tactical_advice(self):
        advice = []
        suspects = sorted([p for p in self.players.values() if not p.real_role and not p.is_dead], 
                          key=lambda x: x.wolf_prob, reverse=True)
        
        if not suspects:
            return "[dim]局势尚未明朗，建议先划水听一轮发言，多记动作。[/]"
            
        prime_suspect = suspects[0]
        
        if prime_suspect.wolf_prob > 70:
            attack_points = []
            for (src, tgt, act) in self.relations:
                if src == prime_suspect.pid:
                    tgt_player = self.players[tgt]
                    if act in ['bad', 'suspect'] and tgt_player.real_role and tgt_player.real_role != 'W':
                        attack_points.append(f"他疯狂攻击过铁好人{tgt}号")
                    elif act == 'vote' and tgt_player.real_role and tgt_player.real_role != 'W':
                        attack_points.append(f"他在关键轮次把票冲在了铁好人{tgt}号身上")
            
            if attack_points:
                reasons = "，且".join(attack_points)
                advice.append(f"[bold red]🔥 建议放逐目标锁定 {prime_suspect.pid}号！[/]")
                advice.append(f"底层逻辑抓狼：强烈建议今天出 {prime_suspect.pid}号。因为{reasons}。这绝对是狼队视角，好人们不要分票！")
            else:
                advice.append(f"[bold red]🔥 重点施压 {prime_suspect.pid}号。[/] 他的整体行为在图谱中极度异常（狼面 {prime_suspect.wolf_prob:.1f}%），听听他怎么辩解。")
                
        bad_voters = set()
        for (src, tgt, act) in self.relations:
            if act == 'vote':
                tgt_player = self.players[tgt]
                src_player = self.players[src]
                if tgt_player.real_role and tgt_player.real_role != 'W' and not src_player.is_dead:
                    bad_voters.add(str(src))
                    
        if bad_voters:
            advice.append(f"[bold yellow]⚠️ 注意冲票反噬：[/] {','.join(bad_voters)} 号玩家曾抱团投给过好人。发言时可以诈他们一下，一律按冲锋/倒钩处理！")

        if not advice:
            return "[dim]暂无压倒性的逻辑爆点，多留意那些发言软但投票凶的人。[/]"
            
        return "\n".join(advice)
    
    def normalize_probabilities(self):
        confirmed_wolves = sum(1 for p in self.players.values() if p.real_role == 'W')
        remaining_wolves = self.wolf_count - confirmed_wolves
        unknown_players = [p for p in self.players.values() if not p.real_role]
        
        if not unknown_players: return
        
        if remaining_wolves <= 0:
            for p in unknown_players: p.wolf_prob = 0.0
            return
            
        current_sum = sum(p.wolf_prob for p in unknown_players)
        if current_sum <= 0:
            avg_prob = (remaining_wolves / len(unknown_players)) * 100.0
            for p in unknown_players: p.wolf_prob = avg_prob
            return
            
        scale_factor = (remaining_wolves * 100.0) / current_sum
        for p in unknown_players:
            p.wolf_prob = min(99.9, max(0.1, p.wolf_prob * scale_factor))

    def parse_command(self, cmd):
        cmd = cmd.strip()
        if not cmd: return
        
        matched = False
        for action, pattern in self.rules.items():
            match = pattern.match(cmd)
            if match:
                self._execute_action(action, match.groups())
                matched = True
                break
                
        if not matched:
            self.add_log(f"[red]语法错误:[/] 未知指令 '{cmd}'", f"语法错误: 未知指令 '{cmd}'")

    def _execute_action(self, action, args):
        action_signature = (action, tuple(args))
        if action_signature in self.history:
            self.logs.append(f"[dim gray]过滤重复:[/] 忽略已记录的重复指令 -> {action_signature}")
            return
        
        self.history.add(action_signature)

        try:
            def check_pid(*pids):
                for p in pids:
                    if int(p) not in self.players:
                        raise KeyError(f"号码 {p} 超出当前板子人数范围")

            if action == "claim":
                pid, role = int(args[0]), args[1].upper()
                check_pid(pid)
                self.players[pid].claim = role
                role_name = "好人/民" if role == 'V' else role
                self.add_log(f"[cyan]声称:[/] {pid}号 认 {role_name}", f"{pid}号 认 {role_name}")
                
            elif action == "good":
                source, target = int(args[0]), int(args[1])
                check_pid(source, target)
                # 防呆补丁：发真金水强行转神职
                if self.players[source].claim not in ['S', 'WI', 'H']: 
                    self.players[source].claim = 'S'
                self.relations.append((source, target, 'good'))
                self.add_log(f"[green]铁金水:[/] {source}号 验出 {target}号 是金水", f"{source}号 验出 {target}号 是金水")
                
            elif action == "bad":
                source, target = int(args[0]), int(args[1])
                check_pid(source, target)
                self.relations.append((source, target, 'bad'))
                self.add_log(f"[red]铁查杀:[/] {source}号 验出/死踩 {target}号 是狼", f"{source}号 验出/死踩 {target}号 是狼")

            elif action == "suspect":
                source, target = int(args[0]), int(args[1])
                check_pid(source, target)
                self.relations.append((source, target, 'suspect'))
                self.add_log(f"[yellow]软怀疑:[/] {source}号 踩/怀疑 {target}号", f"{source}号 踩/怀疑 {target}号")

            elif action == "vouch":
                source, target = int(args[0]), int(args[1])
                check_pid(source, target)
                self.relations.append((source, target, 'vouch'))
                self.add_log(f"[cyan]软保人:[/] {source}号 认 {target}号 是好牌/站边", f"{source}号 认 {target}号 是好牌/站边")

            elif action == "silver":
                source, target = int(args[0]), int(args[1])
                check_pid(source, target)
                # 防呆补丁：发银水强行转女巫
                if self.players[source].claim not in ['S', 'WI', 'H']: 
                    self.players[source].claim = 'WI'
                self.relations.append((source, target, 'silver'))
                self.add_log(f"[blue]女巫银水:[/] {source}号 救了 {target}号 (发银水)", f"{source}号 给 {target}号 发银水")

            elif action == "vote":
                voters = [int(v) for v in args[0].split(',') if v.strip()]
                target = int(args[1])
                check_pid(target, *voters) 
                for voter in voters:
                    self.relations.append((voter, target, 'vote'))
                self.add_log(f"[magenta]果断冲票:[/] {voters} 投给 {target}号", f"{voters} 投给 {target}号")
                
            elif action == "dead":
                pid = int(args[0])
                is_night = bool(args[1])
                check_pid(pid)
                self.players[pid].is_dead = True
                self.players[pid].death_type = "xn" if is_night else "x"
                time_desc = "夜间倒牌" if is_night else "白天出局"
                self.add_log(f"[gray]出局:[/] {pid}号 死亡 ({time_desc})", f"{pid}号 死亡 ({time_desc})")
                
            elif action == "confirm":
                pid, role = int(args[0]), args[1].upper()
                check_pid(pid)
                self.players[pid].real_role = role
                self.add_log(f"[bold magenta]底牌确认:[/] {pid}号 真实身份为 {role}", f"{pid}号 真实身份为 {role}")
            
            self.recalculate_all()
                
        except ValueError:
            self.logs.append("[red]输入错误:[/] 包含非数字或无效字符。")
        except KeyError as e:
            self.logs.append(f"[red]越界拦截:[/] {e}")
        except Exception as e:
            self.logs.append(f"[red]系统拦截:[/] 指令解析异常 ({e})。")

def render_dashboard(engine, console):
    os.system('cls' if os.name == 'nt' else 'clear')
    
    title = f"🐺 狼人杀动态图谱推演终端 v3.5 (完全体) - {engine.player_count}人{engine.wolf_count}狼"
    table = Table(title=title, style="bold white")
    table.add_column("号码", justify="center", style="cyan")
    table.add_column("状态", justify="center")
    table.add_column("声称身份", justify="center", style="green")
    table.add_column("真实身份", justify="center", style="magenta")
    table.add_column("狼面概率估算", justify="center")

    sorted_players = sorted(engine.players.values(), key=lambda x: x.wolf_prob, reverse=True)

    for p in sorted_players:
        if p.is_dead:
            status = "[blue]🌙[/]" if p.death_type == 'xn' else "[red]💀[/]"
        else:
            status = "[green]😊[/]"
            
        prob_color = "red" if p.wolf_prob > 60 else "yellow" if p.wolf_prob > 30 else "green"
        
        if p.real_role == 'W':
            prob_str = "[red]100.0%[/]"
        elif p.real_role:
            prob_str = "[green]0.0%[/]"
        else:
            prob_str = f"[{prob_color}]{p.wolf_prob:.1f}%[/{prob_color}]"
        
        table.add_row(
            str(p.pid), 
            status, 
            p.claim if p.claim else "-", 
            p.real_role if p.real_role else "?", 
            prob_str
        )
    
    console.print(table)
    
    advice_text = engine.get_tactical_advice()
    console.print(Panel(advice_text, title="💡 实战发言军师 (Tactical Advice)", border_style="yellow", width=70))
    
    log_text = "\n".join(engine.logs[-8:]) if engine.logs else "暂无记录..."
    console.print(Panel(log_text, title="最新事件日志 (Event Logs)", width=70))
    
    # === 新增：完整的操作手册面板 ===
    syntax_help = """[bold]速记语法大全:[/]
[cyan]身份/确认:[/] 1=S(认预) 1=V(认民) 7!W(底牌确认为狼)
[green]硬逻辑(神职专属，高权重):[/] 1+2(真金水) 1-3(真查杀) 4@5(女巫发银水)
[yellow]软逻辑(平民口水，低权重):[/] 1*2(软保人/站边) 1~3(软踩/丢进狼坑)
[red]行动/出局(连坐核心):[/] 4,5>1(投票给1号) 6x(白天票死) 6xn(夜间倒牌)
输入 [bold]q[/] 退出并保存TXT复盘日志"""
    console.print(Panel(syntax_help, title="⌨️ 操作终端手册", border_style="cyan", width=70))

def export_logs(engine, console):
    if not engine.raw_logs:
        return
        
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"werewolf_record_{timestamp}.txt"
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"🐺 狼人杀复盘记录 ({engine.player_count}人{engine.wolf_count}狼)\n")
        f.write(f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*40 + "\n")
        for log in engine.raw_logs:
            f.write(log + "\n")
            
    console.print(f"\n[bold green]✅ 比赛已结束，复盘记录已自动保存至: {filename}[/]")

def main():
    console = Console()
    os.system('cls' if os.name == 'nt' else 'clear')
    
    console.print("[bold cyan]🐺 欢迎使用狼人杀图谱辅助推演工具 v3.5[/]")
    p_input = console.input("请输入玩家总数 (直接回车默认9): ")
    player_count = int(p_input) if p_input.strip().isdigit() else 9
    
    w_input = console.input("请输入狼人数量 (直接回车默认3): ")
    wolf_count = int(w_input) if w_input.strip().isdigit() else 3
    
    engine = WerewolfEngine(player_count=player_count, wolf_count=wolf_count)
    
    while True:
        render_dashboard(engine, console)
        cmd = console.input("\n[bold cyan]输入速记指令 > [/]")
        
        if cmd.lower() in ['q', 'quit', 'exit']:
            export_logs(engine, console)
            break
            
        engine.parse_command(cmd)

if __name__ == "__main__":
    main()