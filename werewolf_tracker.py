import re
import os
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
        self.logs = []
        
        # 预编译正则语法糖
        self.rules = {
            "claim": re.compile(r"^(\d+)=([a-zA-Z]+)$"),     
            "good": re.compile(r"^(\d+)\+(\d+)$"),           
            "bad": re.compile(r"^(\d+)\-(\d+)$"),            
            "vote": re.compile(r"^([\d,]+)>(\d+)$"),         
            "dead": re.compile(r"^(\d+)x$"),                 
            "confirm": re.compile(r"^(\d+)!([a-zA-Z]+)$")    
        }

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
            self.logs.append(f"[red]语法错误:[/] 未知指令 '{cmd}'")

    def _execute_action(self, action, args):
        try:
            # 越界检查辅助函数
            def check_pid(*pids):
                for p in pids:
                    if int(p) not in self.players:
                        raise KeyError(f"号码 {p} 超出当前板子人数范围")

            if action == "claim":
                pid, role = int(args[0]), args[1].upper()
                check_pid(pid)
                self.players[pid].claim = role
                role_name = "好人/民" if role == 'V' else role
                self.logs.append(f"[cyan]声称:[/] {pid}号 认 {role_name}")
                
            elif action == "good":
                source, target = int(args[0]), int(args[1])
                check_pid(source, target)
                self.logs.append(f"[green]发水:[/] {source}号 给 {target}号 发金水")
                self.players[target].wolf_prob = max(0, self.players[target].wolf_prob - 10)
                
            elif action == "bad":
                source, target = int(args[0]), int(args[1])
                check_pid(source, target)
                self.logs.append(f"[red]查杀:[/] {source}号 给 {target}号 发查杀")
                self.players[target].wolf_prob = min(100, self.players[target].wolf_prob + 30)
                
            elif action == "vote":
                voters = [int(v) for v in args[0].split(',') if v.strip()]
                target = int(args[1])
                check_pid(target, *voters) # 检查所有投票人和被投人
                self.logs.append(f"[yellow]投票:[/] {voters} 投给 {target}号")
                
            elif action == "dead":
                pid = int(args[0])
                check_pid(pid)
                self.players[pid].is_dead = True
                self.logs.append(f"[gray]出局:[/] {pid}号 死亡")
                
            elif action == "confirm":
                pid, role = int(args[0]), args[1].upper()
                check_pid(pid)
                self.players[pid].real_role = role
                self.players[pid].wolf_prob = 100.0 if role == 'W' else 0.0
                self.logs.append(f"[magenta]确认:[/] {pid}号 真实身份为 {role}")
                
        except ValueError:
            self.logs.append("[red]输入错误:[/] 包含非数字或无效字符。")
        except KeyError as e:
            self.logs.append(f"[red]越界拦截:[/] {e}")
        except Exception as e:
            self.logs.append(f"[red]系统拦截:[/] 指令解析异常 ({e})。")

def render_dashboard(engine, console):
    os.system('cls' if os.name == 'nt' else 'clear')
    
    # 标题动态显示当前板子信息
    title = f"🐺 狼人杀逻辑推演终端 v1.2 ({engine.player_count}人{engine.wolf_count}狼)"
    table = Table(title=title, style="bold white")
    table.add_column("号码", justify="center", style="cyan")
    table.add_column("状态", justify="center")
    table.add_column("声称身份", justify="center", style="green")
    table.add_column("真实身份", justify="center", style="magenta")
    table.add_column("狼概率估算", justify="center")

    for pid, p in engine.players.items():
        status = "[red]💀[/]" if p.is_dead else "[green]😊[/]"
        prob_color = "red" if p.wolf_prob > 60 else "yellow" if p.wolf_prob > 30 else "green"
        prob_str = f"[{prob_color}]{p.wolf_prob:.1f}%[/{prob_color}]"
        
        table.add_row(
            str(pid), 
            status, 
            p.claim if p.claim else "-", 
            p.real_role if p.real_role else "?", 
            prob_str
        )
    
    console.print(table)
    
    log_text = "\n".join(engine.logs[-8:]) if engine.logs else "暂无记录..."
    console.print(Panel(log_text, title="最新事件日志 (Event Logs)", width=65))
    console.print("\n[bold]语法提示:[/] 1=S(跳预), 1=V(认民), 1+2(金水), 1-3(查杀)\n           4,5>1(投票), 6x(死亡), 7!W(确认身份), 输入 q 退出")

def main():
    console = Console()
    os.system('cls' if os.name == 'nt' else 'clear')
    
    # --- 启动向导（动态初始化） ---
    console.print("[bold cyan]🐺 欢迎使用狼人杀终端辅助工具[/]")
    p_input = console.input("请输入玩家总数 (直接回车默认12): ")
    player_count = int(p_input) if p_input.strip().isdigit() else 12
    
    w_input = console.input("请输入狼人数量 (直接回车默认4): ")
    wolf_count = int(w_input) if w_input.strip().isdigit() else 4
    
    engine = WerewolfEngine(player_count=player_count, wolf_count=wolf_count)
    
    while True:
        render_dashboard(engine, console)
        cmd = console.input("\n[bold cyan]输入速记指令 > [/]")
        
        if cmd.lower() in ['q', 'quit', 'exit']:
            break
            
        engine.parse_command(cmd)

if __name__ == "__main__":
    main()