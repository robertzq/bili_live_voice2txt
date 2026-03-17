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
        self.wolf_prob = initial_prob   # 动态初始概率
        self.is_dead = False
        self.real_role = ""     

class WerewolfEngine:
    def __init__(self, player_count=12, wolf_count=4):
        self.player_count = player_count
        self.wolf_count = wolf_count
        
        # 动态计算初始狼人概率
        initial_prob = round((wolf_count / player_count) * 100, 1)
        self.players = {i: Player(i, initial_prob) for i in range(1, player_count + 1)}
        
        self.logs = []          # 用于 UI 显示的带颜色日志
        self.raw_logs = []      # 用于最后导出 txt 的纯净日志
        self.history = set()    # 核心：动作指纹记录池，用于去重
        
        # 预编译正则语法糖
        self.rules = {
            "claim": re.compile(r"^(\d+)=([a-zA-Z]+)$"),     
            "good": re.compile(r"^(\d+)\+(\d+)$"),           
            "bad": re.compile(r"^(\d+)\-(\d+)$"),            
            "vote": re.compile(r"^([\d,]+)>(\d+)$"),         
            "dead": re.compile(r"^(\d+)x$"),                 
            "confirm": re.compile(r"^(\d+)!([a-zA-Z]+)$")    
        }

    # ================= 贝叶斯数学引擎 =================
    def update_bayes(self, target_pid, likelihood_ratio):
        """执行贝叶斯公式更新单人概率 (赔率法)"""
        p = self.players[target_pid].wolf_prob / 100.0
        if p >= 0.999 or p <= 0.001: 
            return
            
        odds = p / (1.0 - p)
        new_odds = odds * likelihood_ratio
        new_p = new_odds / (1.0 + new_odds)
        self.players[target_pid].wolf_prob = new_p * 100.0

    def normalize_probabilities(self):
        """全局归一化：保证未知玩家的概率总和等于剩余狼人数"""
        confirmed_wolves = sum(1 for p in self.players.values() if p.real_role == 'W')
        remaining_wolves = self.wolf_count - confirmed_wolves
        
        unknown_players = [p for p in self.players.values() if not p.real_role]
        if not unknown_players:
            return
            
        if remaining_wolves <= 0:
            for p in unknown_players:
                p.wolf_prob = 0.0
            return
            
        current_sum = sum(p.wolf_prob for p in unknown_players)
        
        if current_sum <= 0:
            avg_prob = (remaining_wolves / len(unknown_players)) * 100.0
            for p in unknown_players:
                p.wolf_prob = avg_prob
            return
            
        target_sum = remaining_wolves * 100.0
        scale_factor = target_sum / current_sum
        
        for p in unknown_players:
            p.wolf_prob = p.wolf_prob * scale_factor
            p.wolf_prob = min(99.9, max(0.1, p.wolf_prob))
    # ==================================================

    def add_log(self, ui_text, raw_text):
        """统一管理日志打印和存储"""
        self.logs.append(ui_text)
        self.raw_logs.append(raw_text)

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
        # ===== 核心：接口幂等性拦截 (防抖去重) =====
        action_signature = (action, tuple(args))
        if action_signature in self.history:
            # 如果是重复指令，只在 UI 提示，不计算概率，也不存入复盘 log
            self.logs.append(f"[dim gray]过滤重复:[/] 忽略已记录的重复指令 -> {action_signature}")
            return
        
        # 记录新的有效指纹
        self.history.add(action_signature)
        # ==========================================

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
                self.add_log(f"[green]发水:[/] {source}号 给 {target}号 发金水", f"{source}号 给 {target}号 发金水")
                self.update_bayes(target, 0.4)
                self.normalize_probabilities()
                
            elif action == "bad":
                source, target = int(args[0]), int(args[1])
                check_pid(source, target)
                self.add_log(f"[red]查杀:[/] {source}号 给 {target}号 发查杀", f"{source}号 给 {target}号 发查杀")
                self.update_bayes(target, 3.0)
                self.normalize_probabilities()
                
            elif action == "vote":
                voters = [int(v) for v in args[0].split(',') if v.strip()]
                target = int(args[1])
                check_pid(target, *voters) 
                self.add_log(f"[yellow]投票:[/] {voters} 投给 {target}号", f"{voters} 投给 {target}号")
                
            elif action == "dead":
                pid = int(args[0])
                check_pid(pid)
                self.players[pid].is_dead = True
                self.add_log(f"[gray]出局:[/] {pid}号 死亡", f"{pid}号 死亡")
                
            elif action == "confirm":
                pid, role = int(args[0]), args[1].upper()
                check_pid(pid)
                self.players[pid].real_role = role
                self.players[pid].wolf_prob = 100.0 if role == 'W' else 0.0
                self.add_log(f"[magenta]确认:[/] {pid}号 真实身份为 {role}", f"{pid}号 真实身份为 {role}")
                self.normalize_probabilities() 
                
        except ValueError:
            self.logs.append("[red]输入错误:[/] 包含非数字或无效字符。")
        except KeyError as e:
            self.logs.append(f"[red]越界拦截:[/] {e}")
        except Exception as e:
            self.logs.append(f"[red]系统拦截:[/] 指令解析异常 ({e})。")

def render_dashboard(engine, console):
    os.system('cls' if os.name == 'nt' else 'clear')
    
    title = f"🐺 狼人杀逻辑推演终端 v2.1 (带防抖去重) - {engine.player_count}人{engine.wolf_count}狼"
    table = Table(title=title, style="bold white")
    table.add_column("号码", justify="center", style="cyan")
    table.add_column("状态", justify="center")
    table.add_column("声称身份", justify="center", style="green")
    table.add_column("真实身份", justify="center", style="magenta")
    table.add_column("狼面概率估算", justify="center")

    # 重点：按概率自动降序排序，揪出最大嫌疑人
    sorted_players = sorted(engine.players.values(), key=lambda x: x.wolf_prob, reverse=True)

    for p in sorted_players:
        status = "[red]💀[/]" if p.is_dead else "[green]😊[/]"
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
    
    log_text = "\n".join(engine.logs[-8:]) if engine.logs else "暂无记录..."
    console.print(Panel(log_text, title="最新事件日志 (Event Logs)", width=70))
    console.print("\n[bold]语法提示:[/] 1=S(跳预), 1=V(认民), 1+2(金水), 1-3(查杀)\n           4,5>1(投票), 6x(死亡), 7!W(确认身份), 输入 q 退出")

def export_logs(engine, console):
    """退出时自动生成复盘文件"""
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
    
    console.print("[bold cyan]🐺 欢迎使用狼人杀终端辅助工具 v2.1[/]")
    p_input = console.input("请输入玩家总数 (直接回车默认12): ")
    player_count = int(p_input) if p_input.strip().isdigit() else 12
    
    w_input = console.input("请输入狼人数量 (直接回车默认4): ")
    wolf_count = int(w_input) if w_input.strip().isdigit() else 4
    
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