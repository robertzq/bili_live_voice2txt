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
        
        # 预编译正则语法糖 (扩展了夜间死亡 xn)
        self.rules = {
            "claim": re.compile(r"^(\d+)=([a-zA-Z]+)$"),     
            "good": re.compile(r"^(\d+)\+(\d+)$"),           
            "bad": re.compile(r"^(\d+)\-(\d+)$"),            
            "vote": re.compile(r"^([\d,]+)>(\d+)$"),         
            "dead": re.compile(r"^(\d+)x([nN]?)$"),          # 兼容 6x 和 6xn
            "confirm": re.compile(r"^(\d+)!([a-zA-Z]+)$")    
        }

    def add_log(self, ui_text, raw_text):
        self.logs.append(ui_text)
        self.raw_logs.append(raw_text)

    # ================= 全新：图谱推演引擎 =================
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
        # 1. 重置所有未亮明身份的玩家概率到基线
        for p in self.players.values():
            if p.real_role:
                p.wolf_prob = 100.0 if p.real_role == 'W' else 0.0
            else:
                p.wolf_prob = self.base_prob

        # 2. 迭代推演 (循环3次让信任链可以顺藤摸瓜传递)
        for _ in range(3):
            # --- [逻辑A]：全神职动态对跳互斥 (暗牌局抓狼核心) ---
            # 自动提取场上所有声明过，且不是平民('V')的身份标签
            special_claims = set(p.claim for p in self.players.values() if p.claim and p.claim != 'V')
            
            for role in special_claims:
                claimers = [p for p in self.players.values() if p.claim == role]
                if len(claimers) > 1:
                    # 发现对跳！(比如两个S，或者两个H)
                    good_claimers = [p for p in claimers if p.real_role and p.real_role != 'W']
                    if good_claimers: 
                        # 如果其中一个已经被确认为好人，另一个直接标狼
                        for c in claimers:
                            if not c.real_role: self._apply_odds(c, 20.0)
                    else:
                        # 暗牌模式：双方都没确认，系统给双方同时施加互斥高压！
                        # 每轮循环放大 1.5 倍，3轮就是 3.375 倍的嫌疑增长
                        for c in claimers:
                            if not c.real_role: self._apply_odds(c, 1.5)

            # --- [逻辑B]：图谱连坐与倒推 ---
            for (src_id, tgt_id, act_type) in self.relations:
                src = self.players[src_id]
                tgt = self.players[tgt_id]
                
                p_src_w, p_src_g = src.wolf_prob / 100.0, 1.0 - (src.wolf_prob / 100.0)
                p_tgt_w, p_tgt_g = tgt.wolf_prob / 100.0, 1.0 - (tgt.wolf_prob / 100.0)

                if act_type == 'bad':
                    if not tgt.real_role:
                        self._apply_odds(tgt, p_src_g * 2.0 + p_src_w * 0.5)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_g * 1.8 + p_tgt_w * 0.5)

                elif act_type == 'good':
                    if not tgt.real_role:
                        self._apply_odds(tgt, p_src_g * 0.2 + p_src_w * 2.5)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_g * 1.0 + p_tgt_w * 5.0)

                elif act_type == 'vote':
                    if not tgt.real_role:
                        self._apply_odds(tgt, p_src_g * 1.5 + p_src_w * 0.6)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_g * 1.5 + p_tgt_w * 0.5)

        # 3. [终极高阶逻辑]：双金水定理 & 残局防倒钩反弹
        alive_players = [p for p in self.players.values() if not p.is_dead]
        
        for p in self.players.values():
            if p.real_role: continue
            
            # 【修复】：检测真正的双金水 (只有声称是 S 的人发的才算！)
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
        """基于当前局势生成实时发言建议"""
        advice = []
        
        # 1. 寻找核心突破口 (最高嫌疑人)
        suspects = sorted([p for p in self.players.values() if not p.real_role and not p.is_dead], 
                          key=lambda x: x.wolf_prob, reverse=True)
        
        if not suspects:
            return "[dim]局势尚未明朗，建议先划水听一轮发言，不要盲目站边。[/]"
            
        prime_suspect = suspects[0]
        
        # 2. 针对最高嫌疑人生成攻击话术
        if prime_suspect.wolf_prob > 70:
            attack_points = []
            # 查图谱，看他干了什么坏事
            for (src, tgt, act) in self.relations:
                if src == prime_suspect.pid:
                    tgt_player = self.players[tgt]
                    if act == 'bad' and tgt_player.real_role and tgt_player.real_role != 'W':
                        attack_points.append(f"他给铁好人{tgt}号发过查杀/死踩")
                    elif act == 'vote' and tgt_player.real_role and tgt_player.real_role != 'W':
                        attack_points.append(f"他在关键轮次把票挂在了铁好人{tgt}号身上")
            
            if attack_points:
                reasons = "，且".join(attack_points)
                advice.append(f"[bold red]🔥 攻击目标锁定 {prime_suspect.pid}号！[/]")
                advice.append(f"发言思路：强烈建议今天出 {prime_suspect.pid}号。不仅因为他状态差，更因为{reasons}。这绝对是狼人视角的行为，好人们不要被带偏，今天全票打飞 {prime_suspect.pid}！")
            else:
                advice.append(f"[bold red]🔥 重点关注 {prime_suspect.pid}号。[/] 他的整体行为极度异常（狼面 {prime_suspect.wolf_prob:.1f}%），发言时可以稍微施压，听他怎么辩解。")
                
        # 3. 寻找抱团冲票的线索
        # 统计有哪些活着的人，把票投给了已知的好人
        bad_voters = set()
        for (src, tgt, act) in self.relations:
            if act == 'vote':
                tgt_player = self.players[tgt]
                src_player = self.players[src]
                if tgt_player.real_role and tgt_player.real_role != 'W' and not src_player.is_dead:
                    bad_voters.add(str(src))
                    
        if bad_voters:
            advice.append(f"[bold yellow]⚠️ 注意票型反噬：[/] {','.join(bad_voters)} 号玩家曾把票投给了好人。发言时可以质问他们：'你们当时为什么给好人冲票？请给出合理的逻辑，否则一律按倒钩狼处理！'")

        if not advice:
            return "[dim]暂无强烈逻辑爆点，建议多盘一盘已知神牌的逻辑线。[/]"
            
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
    # ==================================================

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
                # 记录图谱关系
                self.relations.append((source, target, 'good'))
                self.add_log(f"[green]发水:[/] {source}号 给 {target}号 发金水", f"{source}号 给 {target}号 发金水")
                
            elif action == "bad":
                source, target = int(args[0]), int(args[1])
                check_pid(source, target)
                # 记录图谱关系
                self.relations.append((source, target, 'bad'))
                self.add_log(f"[red]查杀:[/] {source}号 给 {target}号 发查杀", f"{source}号 给 {target}号 发查杀")
                
            elif action == "vote":
                voters = [int(v) for v in args[0].split(',') if v.strip()]
                target = int(args[1])
                check_pid(target, *voters) 
                
                # 【新增】：将每个人投给谁，拆分成独立的图谱关系存入引擎
                for voter in voters:
                    self.relations.append((voter, target, 'vote'))
                    
                self.add_log(f"[yellow]投票:[/] {voters} 投给 {target}号", f"{voters} 投给 {target}号")
                
            elif action == "dead":
                pid = int(args[0])
                is_night = bool(args[1]) # 如果输入 6xn, 这里会识别到 n
                check_pid(pid)
                self.players[pid].is_dead = True
                self.players[pid].death_type = "xn" if is_night else "x"
                time_desc = "夜间倒牌" if is_night else "白天出局"
                self.add_log(f"[gray]出局:[/] {pid}号 死亡 ({time_desc})", f"{pid}号 死亡 ({time_desc})")
                
            elif action == "confirm":
                pid, role = int(args[0]), args[1].upper()
                check_pid(pid)
                self.players[pid].real_role = role
                self.add_log(f"[magenta]确认:[/] {pid}号 真实身份为 {role}", f"{pid}号 真实身份为 {role}")
            
            # 无论发生什么有效动作，全部重新推演信任链图谱！
            self.recalculate_all()
                
        except ValueError:
            self.logs.append("[red]输入错误:[/] 包含非数字或无效字符。")
        except KeyError as e:
            self.logs.append(f"[red]越界拦截:[/] {e}")
        except Exception as e:
            self.logs.append(f"[red]系统拦截:[/] 指令解析异常 ({e})。")

def render_dashboard(engine, console):
    os.system('cls' if os.name == 'nt' else 'clear')
    
    title = f"🐺 狼人杀动态图谱推演终端 v3.0 - {engine.player_count}人{engine.wolf_count}狼"
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
    # === 新增：显示战术建议面板 ===
    advice_text = engine.get_tactical_advice()
    console.print(Panel(advice_text, title="💡 实战发言军师 (Tactical Advice)", border_style="yellow", width=70))
    # ==============================
    
    log_text = "\n".join(engine.logs[-8:]) if engine.logs else "暂无记录..."
    console.print(Panel(log_text, title="最新事件日志 (Event Logs)", width=70))
    console.print("\n[bold]语法提示:[/] 1=S(预) 1=V(民) 1+2(金水) 1-3(查杀)\n           4,5>1(投票) 6x(票死) 6xn(夜里死) 7!W(确认身份) q退出")

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
    
    console.print("[bold cyan]🐺 欢迎使用狼人杀图谱辅助推演工具 v3.0[/]")
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