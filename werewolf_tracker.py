import os
import datetime
import tkinter as tk
from tkinter import ttk, messagebox

class Player:
    def __init__(self, pid, initial_prob):
        self.pid = pid
        self.claim = ""         
        self.wolf_prob = initial_prob
        self.is_dead = False
        self.death_type = ""
        self.real_role = ""     

class WerewolfEngine:
    def __init__(self, player_count=9, wolf_count=3):
        self.player_count = player_count
        self.wolf_count = wolf_count
        self.base_prob = round((wolf_count / player_count) * 100, 1)
        self.reset_state()

    def reset_state(self):
        self.players = {i: Player(i, self.base_prob) for i in range(1, self.player_count + 1)}
        self.logs = []
        self.relations = []
        self.applied_actions = []

    def undo_last_action(self):
        if not self.applied_actions:
            return False
        self.applied_actions.pop()
        
        saved_actions = list(self.applied_actions)
        self.reset_state()
        
        for act in saved_actions:
            self.apply_action(*act, is_replay=True)
        return True

    def _apply_odds(self, player, ratio):
        p = player.wolf_prob / 100.0
        if p >= 0.999 or p <= 0.001: return
        odds = p / (1.0 - p)
        new_odds = odds * ratio
        new_p = new_odds / (1.0 + new_odds)
        player.wolf_prob = new_p * 100.0

    def recalculate_all(self):
        for p in self.players.values():
            if p.real_role:
                p.wolf_prob = 100.0 if p.real_role == 'W' else 0.0
            else:
                p.wolf_prob = self.base_prob

        for _ in range(3):
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

            for (src_id, tgt_id, act_type) in self.relations:
                src = self.players[src_id]
                tgt = self.players[tgt_id]
                p_src_w, p_src_g = src.wolf_prob / 100.0, 1.0 - (src.wolf_prob / 100.0)
                p_tgt_w, p_tgt_g = tgt.wolf_prob / 100.0, 1.0 - (tgt.wolf_prob / 100.0)

                if act_type == 'bad':
                    if not tgt.real_role: self._apply_odds(tgt, p_src_g * 5.0 + p_src_w * 0.2)
                    if not src.real_role: self._apply_odds(src, p_tgt_g * 5.0 + p_tgt_w * 0.5)
                elif act_type == 'good':
                    if not tgt.real_role: self._apply_odds(tgt, p_src_g * 0.2 + p_src_w * 3.0)
                    if not src.real_role: self._apply_odds(src, p_tgt_g * 0.8 + p_tgt_w * 5.0)
                elif act_type == 'suspect':
                    if not tgt.real_role: self._apply_odds(tgt, p_src_g * 1.05 + p_src_w * 0.8)
                    if not src.real_role: self._apply_odds(src, p_tgt_g * 1.1 + p_tgt_w * 0.9)
                elif act_type == 'vouch':
                    if not tgt.real_role: self._apply_odds(tgt, p_src_g * 1.0 + p_src_w * 1.4)
                    if not src.real_role: self._apply_odds(src, p_tgt_w * 1.8 + p_tgt_g * 0.9)
                elif act_type == 'silver':
                    if not tgt.real_role: self._apply_odds(tgt, p_src_g * 0.3 + p_src_w * 2.0)
                    if not src.real_role: self._apply_odds(src, p_tgt_w * 2.5 + p_tgt_g * 0.5)
                elif act_type == 'vote':
                    if not tgt.real_role:
                        if src.real_role and src.real_role != 'W':
                            mult_fwd = 1.0 
                        else:
                            mult_fwd = p_src_g * 1.1 + p_src_w * 0.7
                        self._apply_odds(tgt, mult_fwd)
                    if not src.real_role:
                        self._apply_odds(src, p_tgt_g * 1.5 + p_tgt_w * 0.6)

        alive_players = [p for p in self.players.values() if not p.is_dead]
        for p in self.players.values():
            if p.real_role: continue
            good_sources = [src for src, tgt, act in self.relations if act == 'good' and tgt == p.pid]
            seer_sources = [src for src in good_sources if self.players[src].claim == 'S']
            if len(set(seer_sources)) >= 2:
                p.wolf_prob = 1.0 
                p.claim = f"{p.claim}(双金水)" if p.claim and "(双金水)" not in p.claim else p.claim or "(双金水)" 
                continue

            if len(alive_players) <= 4 and p.wolf_prob < 15.0 and not p.is_dead:
                voted_for_dead = any(tgt for src, tgt, act in self.relations if src == p.pid and act == 'vote' and self.players[tgt].is_dead)
                if voted_for_dead:
                    p.wolf_prob = max(p.wolf_prob, 30.0)
                    p.claim = f"{p.claim}[⚠️疑倒钩]" if p.claim and "[⚠️疑倒钩]" not in p.claim else p.claim or "[⚠️疑倒钩]"

        self.normalize_probabilities()

    def normalize_probabilities(self):
        confirmed_wolves = sum(1 for p in self.players.values() if p.real_role == 'W')
        rem_wolves = self.wolf_count - confirmed_wolves
        unknown = [p for p in self.players.values() if not p.real_role]
        if not unknown: return
        if rem_wolves <= 0:
            for p in unknown: p.wolf_prob = 0.0
            return
        c_sum = sum(p.wolf_prob for p in unknown)
        if c_sum <= 0:
            avg = (rem_wolves / len(unknown)) * 100.0
            for p in unknown: p.wolf_prob = avg
            return
        scale = (rem_wolves * 100.0) / c_sum
        for p in unknown: p.wolf_prob = min(99.9, max(0.1, p.wolf_prob * scale))

    def get_tactical_advice(self):
        advice = []
        suspects = sorted([p for p in self.players.values() if not p.real_role and not p.is_dead], key=lambda x: x.wolf_prob, reverse=True)
        if not suspects: return "局势尚未明朗，建议先划水听一轮发言，多记动作。"
        prime = suspects[0]
        
        if prime.wolf_prob > 70:
            pts = []
            for (src, tgt, act) in self.relations:
                if src == prime.pid:
                    tgt_p = self.players[tgt]
                    if act in ['bad', 'suspect'] and tgt_p.real_role and tgt_p.real_role != 'W':
                        pts.append(f"攻击过铁好人{tgt}号")
                    elif act == 'vote' and tgt_p.real_role and tgt_p.real_role != 'W':
                        pts.append(f"把票冲在了铁好人{tgt}号身上")
            if pts:
                advice.append(f"🔥 建议放逐锁定 {prime.pid}号！ 因为{'，且'.join(pts)}。这是狼视角，不要分票！")
            else:
                advice.append(f"🔥 重点施压 {prime.pid}号！整体行为在图谱中异常(狼面{prime.wolf_prob:.1f}%)。")
                
        bad_voters = set(str(src) for src, tgt, act in self.relations if act == 'vote' and self.players[tgt].real_role and self.players[tgt].real_role != 'W' and not self.players[src].is_dead)
        if bad_voters: advice.append(f"⚠️ 注意冲票反噬：{','.join(bad_voters)} 号曾抱团投给过好人。按倒钩/冲锋处理！")

        return "\n".join(advice) if advice else "暂无压倒性的逻辑爆点，多留意那些发言软但投票凶的人。"

    def apply_action(self, action, src_str, tgt_str, is_replay=False):
        if not is_replay:
            self.applied_actions.append((action, src_str, tgt_str))

        def check_pid(p_str):
            try:
                pid = int(p_str)
            except ValueError:
                raise ValueError(f"'{p_str}' 不是有效的纯数字")
            if pid not in self.players: raise ValueError(f"号码 {pid} 超出范围")
            return pid

        log_msg_list = []
        try:
            if action in ["dead_day", "dead_night"]:
                if src_str and not tgt_str:
                    tgt_str = src_str
                    src_str = "1" 
                elif not src_str:
                    src_str = "1"

            normalized_src = src_str.replace('，', ' ').replace(',', ' ')
            src_list = [v.strip() for v in normalized_src.split() if v.strip()]
            
            if not src_list or not tgt_str.strip():
                raise ValueError("请确保左右两边对应的号码/身份已填写！")

            tgt_val = tgt_str.strip()
            voters_list = []

            for src_val in src_list:
                if action == "claim":
                    src = check_pid(src_val)
                    role = tgt_val.upper()
                    self.players[src].claim = role
                    log_msg_list.append(f"声称: {src}号 认 {role}")

                elif action == "confirm":
                    src = check_pid(src_val)
                    role = tgt_val.upper()
                    self.players[src].real_role = role
                    log_msg_list.append(f"确认: {src}号 底牌为 {role}")

                elif action in ["good", "bad", "suspect", "vouch", "silver"]:
                    src = check_pid(src_val)
                    tgt = check_pid(tgt_val)
                    if action == "good":
                        if self.players[src].claim not in ['S', 'WI', 'H']: self.players[src].claim = 'S'
                        log_msg_list.append(f"金水: {src}号 验出 {tgt}号 是金水")
                    elif action == "bad":
                        log_msg_list.append(f"查杀: {src}号 查杀/死踩 {tgt}号")
                    elif action == "suspect":
                        log_msg_list.append(f"软踩: {src}号 怀疑/轻踩 {tgt}号")
                    elif action == "vouch":
                        log_msg_list.append(f"软保: {src}号 站边/保 {tgt}号")
                    elif action == "silver":
                        if self.players[src].claim not in ['S', 'WI', 'H']: self.players[src].claim = 'WI'
                        log_msg_list.append(f"银水: {src}号(女巫) 救了 {tgt}号")
                    self.relations.append((src, tgt, action))

                elif action == "vote":
                    src = check_pid(src_val)
                    tgt = check_pid(tgt_val)
                    self.relations.append((src, tgt, 'vote'))
                    voters_list.append(str(src))

                elif action == "dead_day":
                    tgt = check_pid(tgt_val)
                    self.players[tgt].is_dead = True
                    self.players[tgt].death_type = "x"
                    log_msg_list.append(f"出局: {tgt}号 白天被票死")
                    break 

                elif action == "dead_night":
                    tgt = check_pid(tgt_val)
                    self.players[tgt].is_dead = True
                    self.players[tgt].death_type = "xn"
                    log_msg_list.append(f"倒牌: {tgt}号 夜间死亡")
                    break 

                elif action == "poison":
                    src = check_pid(src_val)
                    tgt = check_pid(tgt_val)
                    self.players[src].claim = 'WI'
                    self.players[tgt].is_dead = True
                    self.players[tgt].death_type = "xn"
                    log_msg_list.append(f"撒毒: {src}号(女巫) 毒死了 {tgt}号")

                elif action == "shoot":
                    src = check_pid(src_val)
                    tgt = check_pid(tgt_val)
                    self.players[src].real_role = 'H' 
                    self.players[tgt].is_dead = True
                    self.players[tgt].death_type = "x"
                    log_msg_list.append(f"开枪: {src}号(明猎人) 开枪带走了 {tgt}号")

            if action == "vote" and voters_list:
                log_msg_list = [f"投票: [{', '.join(voters_list)}] 一起投给了 {tgt_val}号"]

            final_log = "\n".join(log_msg_list)
            self.logs.append(final_log)
            self.recalculate_all()
            return True, final_log
            
        except Exception as e:
            if not is_replay: self.applied_actions.pop()
            return False, str(e)


class WerewolfGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("🐺 狼人杀动态图谱推演终端 V4.3 (带保存版)")
        self.root.geometry("1100x850")
        self.root.configure(bg="#2E3440")
        
        self.style = ttk.Style()
        if 'clam' in self.style.theme_names():
            self.style.theme_use('clam')
            
        self.style.configure("TFrame", background="#2E3440")
        self.style.configure("TLabel", background="#2E3440", foreground="#D8DEE9", font=("微软雅黑", 11))
        self.style.configure("Action.TButton", font=("微软雅黑", 11, "bold"), padding=6)
        
        self.style.configure("Treeview", font=("微软雅黑", 11), rowheight=30, background="#3B4252", foreground="#ECEFF4", fieldbackground="#3B4252")
        self.style.configure("Treeview.Heading", font=("微软雅黑", 12, "bold"), background="#4C566A", foreground="#ECEFF4")
        
        self.engine = WerewolfEngine(9, 3)
        self.create_widgets()
        self.update_dashboard()

    def create_widgets(self):
        # ================= 顶部：板子设置面板 =================
        setup_frame = ttk.Frame(self.root)
        setup_frame.pack(fill=tk.X, padx=20, pady=10)
        
        ttk.Label(setup_frame, text="总人数:").pack(side=tk.LEFT, padx=(0, 5))
        self.p_entry = ttk.Entry(setup_frame, width=5, font=("微软雅黑", 12), justify="center")
        self.p_entry.insert(0, "9")
        self.p_entry.pack(side=tk.LEFT, padx=(0, 20))
        
        ttk.Label(setup_frame, text="狼人数:").pack(side=tk.LEFT, padx=(0, 5))
        self.w_entry = ttk.Entry(setup_frame, width=5, font=("微软雅黑", 12), justify="center")
        self.w_entry.insert(0, "3")
        self.w_entry.pack(side=tk.LEFT, padx=(0, 20))
        
        reset_btn = ttk.Button(setup_frame, text="🔄 重置图谱并重新开局", style="Action.TButton", command=self.reset_game)
        reset_btn.pack(side=tk.LEFT)

        # ================= 上部：数据大盘 =================
        top_frame = ttk.Frame(self.root)
        top_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        cols = ("pid", "status", "claim", "role", "prob")
        self.tree = ttk.Treeview(top_frame, columns=cols, show="headings", height=10)
        self.tree.heading("pid", text="号码")
        self.tree.heading("status", text="状态")
        self.tree.heading("claim", text="声称身份")
        self.tree.heading("role", text="真实身份")
        self.tree.heading("prob", text="狼面概率估算")
        
        self.tree.column("pid", width=80, anchor="center")
        self.tree.column("status", width=80, anchor="center")
        self.tree.column("claim", width=150, anchor="center")
        self.tree.column("role", width=100, anchor="center")
        self.tree.column("prob", width=150, anchor="center")
        
        self.tree.tag_configure("wolf", foreground="#BF616A")
        self.tree.tag_configure("good", foreground="#A3BE8C")
        self.tree.tag_configure("dead", foreground="#4C566A")
        self.tree.tag_configure("warn", foreground="#EBCB8B")

        self.tree.pack(fill=tk.BOTH, expand=True)

        # ================= 中部：军师与日志 =================
        mid_frame = ttk.Frame(self.root)
        mid_frame.pack(fill=tk.X, padx=20, pady=5)
        
        self.advice_text = tk.Text(mid_frame, height=4, bg="#3B4252", fg="#EBCB8B", font=("微软雅黑", 11, "bold"), wrap=tk.WORD)
        self.advice_text.pack(fill=tk.X, pady=(0, 5))
        
        self.log_text = tk.Text(mid_frame, height=6, bg="#2E3440", fg="#D8DEE9", font=("微软雅黑", 10), wrap=tk.WORD)
        self.log_text.pack(fill=tk.X)

        # ================= 下部：操作面板 =================
        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill=tk.X, padx=20, pady=15)

        # 左侧输入 (源)
        left_frame = ttk.Frame(bottom_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(left_frame, text="发起者\n(用空格分隔，支持批量)").pack(pady=5)
        self.src_entry = ttk.Entry(left_frame, font=("微软雅黑", 16), width=12, justify="center")
        self.src_entry.pack(ipady=10)

        # 中间按钮矩阵
        btn_frame = ttk.Frame(bottom_frame)
        btn_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=15)
        
        buttons = [
            ("认身份 (S/V/WI)", "claim"), ("确认底牌 (H/W)", "confirm"), ("👉 投票冲锋", "vote"),
            ("发金水 (+)", "good"), ("发银水 (@)", "silver"), ("发查杀 (-)", "bad"),
            ("软保人 (*)", "vouch"), ("软踩/怀疑 (~)", "suspect"), ("🔫 猎人带走", "shoot"),
            ("白天票死", "dead_day"), ("🌙 夜间倒牌", "dead_night"), ("☠️ 女巫毒死", "poison")
        ]
        
        for i, (text, act) in enumerate(buttons):
            row, col = divmod(i, 3)
            btn = ttk.Button(btn_frame, text=text, style="Action.TButton", 
                             command=lambda a=act: self.execute_gui_action(a))
            btn.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            
        for i in range(3): btn_frame.columnconfigure(i, weight=1)

        # 右侧输入 (目标)
        right_frame = ttk.Frame(bottom_frame)
        right_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(right_frame, text="目标\n(号码 / 身份字母)").pack(pady=5)
        self.tgt_entry = ttk.Entry(right_frame, font=("微软雅黑", 16), width=8, justify="center")
        self.tgt_entry.pack(ipady=10)

        # 最右侧：系统操作
        sys_frame = ttk.Frame(bottom_frame)
        sys_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=10)
        
        undo_btn = ttk.Button(sys_frame, text="↩️ 撤销上一步", style="Action.TButton", command=self.undo_action)
        undo_btn.pack(pady=5, fill=tk.X, ipady=3)
        
        clear_btn = ttk.Button(sys_frame, text="🗑️ 清空输入框", style="Action.TButton", command=self.clear_inputs)
        clear_btn.pack(pady=5, fill=tk.X, ipady=3)
        
        # 【新增】：保存复盘日志按钮
        save_btn = ttk.Button(sys_frame, text="💾 保存复盘日志", style="Action.TButton", command=self.export_logs)
        save_btn.pack(pady=5, fill=tk.X, ipady=3)

    def reset_game(self):
        try:
            p_count = int(self.p_entry.get())
            w_count = int(self.w_entry.get())
            if p_count <= w_count:
                raise ValueError("狼人不能比总人数还多！")
            
            self.engine = WerewolfEngine(p_count, w_count)
            self.update_dashboard()
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, f"[系统] 🔄 已重新开局：当前板子为 {p_count}人 {w_count}狼。\n")
        except ValueError as e:
            messagebox.showerror("设置错误", "请输入有效的数字！")

    def execute_gui_action(self, action):
        src = self.src_entry.get().strip()
        tgt = self.tgt_entry.get().strip()
        
        success, msg = self.engine.apply_action(action, src, tgt)
        if success:
            self.clear_inputs()
            self.update_dashboard()
        else:
            messagebox.showerror("动作失败", msg)

    def undo_action(self):
        if self.engine.undo_last_action():
            self.update_dashboard()
            self.log_text.insert(tk.END, "\n[系统] ↩️ 已撤销最后一次操作，时间线回退完成。")
            self.log_text.see(tk.END)
        else:
            messagebox.showinfo("无法撤销", "当前已经是初始状态，无记录可撤销。")

    def clear_inputs(self):
        self.src_entry.delete(0, tk.END)
        self.tgt_entry.delete(0, tk.END)
        self.src_entry.focus()
        
    def export_logs(self):
        """导出当前的完整对局日志到 TXT"""
        if not self.engine.logs:
            messagebox.showinfo("提示", "当前没有操作记录，无法保存。")
            return
            
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"werewolf_record_{timestamp}.txt"
            
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"🐺 狼人杀复盘记录 (GUI V4.3 - {self.engine.player_count}人{self.engine.wolf_count}狼)\n")
                f.write(f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*40 + "\n")
                for log in self.engine.logs:
                    f.write(log + "\n")
                    
            messagebox.showinfo("保存成功", f"✅ 复盘记录已成功保存至:\n{os.path.abspath(filename)}")
        except Exception as e:
            messagebox.showerror("保存失败", f"导出文件时发生错误: {e}")

    def update_dashboard(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        sorted_players = sorted(self.engine.players.values(), key=lambda x: x.wolf_prob, reverse=True)
        
        for p in sorted_players:
            tag = "good"
            if p.is_dead: 
                status = "🌙(夜)" if p.death_type == 'xn' else "💀(白)"
                tag = "dead"
            else:
                status = "😊存活"
                if p.wolf_prob > 60: tag = "wolf"
                elif p.wolf_prob > 25: tag = "warn"

            if p.real_role == 'W': prob_str = "100.0% (铁狼)"
            elif p.real_role: prob_str = "0.0% (铁好人)"
            else: prob_str = f"{p.wolf_prob:.1f}%"

            self.tree.insert("", tk.END, values=(p.pid, status, p.claim, p.real_role, prob_str), tags=(tag,))

        self.advice_text.delete(1.0, tk.END)
        self.advice_text.insert(tk.END, "💡 战术军师建议：\n" + self.engine.get_tactical_advice())
        
        self.log_text.delete(1.0, tk.END)
        logs = "\n".join(self.engine.logs[-10:]) if self.engine.logs else "暂无记录..."
        self.log_text.insert(tk.END, logs)
        self.log_text.see(tk.END)

if __name__ == "__main__":
    root = tk.Tk()
    app = WerewolfGUI(root)
    root.mainloop()