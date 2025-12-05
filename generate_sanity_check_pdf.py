#!/usr/bin/env python3
"""
Generate PDF: Melissa's Sanity Check Response to HossAgent Alignment Doc
"""

from fpdf import FPDF
from datetime import datetime

class SanityCheckPDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, 'HossAgent Alignment Doc - Sanity Check Response', 0, 1, 'C')
        self.ln(2)
    
    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')
    
    def section_title(self, title):
        self.set_font('Helvetica', 'B', 14)
        self.set_text_color(0, 0, 0)
        self.ln(5)
        self.cell(0, 10, title, 0, 1, 'L')
        self.set_draw_color(34, 197, 94)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)
    
    def subsection_title(self, title):
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(50, 50, 50)
        self.ln(3)
        self.cell(0, 8, title, 0, 1, 'L')
        self.ln(2)
    
    def body_text(self, text):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 6, text)
        self.ln(2)
    
    def bullet_point(self, text, indent=10):
        self.set_font('Helvetica', '', 10)
        self.set_text_color(30, 30, 30)
        self.set_x(indent)
        self.cell(5, 6, chr(149), 0, 0)
        self.multi_cell(0, 6, text)
    
    def table_row(self, col1, col2, col3, header=False):
        if header:
            self.set_font('Helvetica', 'B', 9)
            self.set_fill_color(240, 240, 240)
        else:
            self.set_font('Helvetica', '', 9)
            self.set_fill_color(255, 255, 255)
        
        self.set_text_color(30, 30, 30)
        col_widths = [45, 60, 85]
        
        self.cell(col_widths[0], 8, col1, 1, 0, 'L', fill=header)
        self.cell(col_widths[1], 8, col2, 1, 0, 'L', fill=header)
        self.cell(col_widths[2], 8, col3, 1, 1, 'L', fill=header)
    
    def status_row(self, component, status, notes):
        self.set_font('Helvetica', '', 9)
        self.set_text_color(30, 30, 30)
        col_widths = [45, 60, 85]
        
        self.cell(col_widths[0], 8, component, 1, 0, 'L')
        
        if "Accurate" in status:
            self.set_text_color(34, 197, 94)
        self.cell(col_widths[1], 8, status, 1, 0, 'L')
        self.set_text_color(30, 30, 30)
        
        self.cell(col_widths[2], 8, notes, 1, 1, 'L')

def generate_pdf():
    pdf = SanityCheckPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    
    pdf.set_font('Helvetica', 'B', 20)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 15, 'Section 8 Response', 0, 1, 'C')
    pdf.set_font('Helvetica', 'I', 12)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, "Melissa's Sanity Check of the HossAgent Alignment Doc", 0, 1, 'C')
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 8, f"Date: {datetime.now().strftime('%B %d, %Y')}", 0, 1, 'C')
    pdf.ln(10)
    
    pdf.section_title('1. Architecture Accuracy')
    pdf.body_text('Mostly correct. A few clarifications:')
    pdf.ln(3)
    
    pdf.table_row('Component', 'Your Description', 'Actual State', header=True)
    pdf.status_row('SignalNet', 'Accurate', 'News + Reddit sources, weather disabled')
    pdf.status_row('LeadEngine', 'Accurate', 'Signals to LeadEvents, threshold 60+')
    pdf.status_row('ARCHANGEL', 'Accurate', 'NAMESTORM + DOMAINSTORM + PHONESTORM')
    pdf.status_row('Outbound', 'Accurate', 'SendGrid via hossagent.net')
    pdf.status_row('Portal/Admin', 'Accurate', 'Portal: OUTBOUND_SENT, Admin: all')
    
    pdf.ln(5)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 6, 'Missing from doc:', 0, 1)
    pdf.body_text('The enrichment_attempts counter exists on LeadEvent but is not yet wired into a formal max_attempts budget system.')
    
    pdf.section_title('2. Enrichment Behavior')
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 6, 'Partially updated since this doc was written:', 0, 1)
    pdf.ln(2)
    
    pdf.bullet_point('NAMESTORM: Now implemented with 5 extraction layers (schema.org, NER patterns, meta tags, heuristics, article body)')
    pdf.bullet_point('DOMAINSTORM: Now has DuckDuckGo HTML search as Layer 4 with backoff/throttle protection')
    pdf.bullet_point('PHONESTORM LITE: Integrated - runs after domain discovery, extracts/classifies phone types')
    
    pdf.ln(3)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 6, "What's still missing (your doc nails this):", 0, 1)
    pdf.ln(2)
    
    pdf.bullet_point('True multi-pass recursion (retry failed leads with new strategies)')
    pdf.bullet_point('Formal budget system (max_attempts -> ARCHIVED_UNENRICHABLE)')
    pdf.bullet_point('"Mission log" tracking URLs/queries already tried')
    
    pdf.section_title('3. Status Lifecycle')
    pdf.body_text('These are the current states:')
    pdf.ln(2)
    
    pdf.set_font('Courier', '', 9)
    pdf.set_x(15)
    pdf.multi_cell(0, 5, 'UNENRICHED -> WITH_DOMAIN_NO_EMAIL -> ENRICHED_NO_OUTBOUND -> OUTBOUND_SENT\n                                                           \\-> ARCHIVED (stale)')
    pdf.ln(3)
    
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 6, 'Missing state you identified:', 0, 1)
    pdf.body_text('ARCHIVED_UNENRICHABLE with explicit reason - this does not exist yet. Currently stale leads get archived but without "we exhausted all options" reasoning.')
    
    pdf.section_title('4. Metrics / Current Numbers')
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(0, 6, 'Directionally correct:', 0, 1)
    pdf.ln(2)
    
    pdf.bullet_point('4,400+ signals over ~48 hours - CONFIRMED')
    pdf.bullet_point('300+ LeadEvents - CONFIRMED')
    pdf.bullet_point('~6 successfully enriched/emailed - CONFIRMED')
    pdf.bullet_point('1-2% enrichment rate - CONFIRMED')
    
    pdf.section_title('5. Constraints')
    pdf.body_text('Key constraints to respect:')
    pdf.ln(3)
    
    pdf.table_row('Constraint', 'Current Handling', '', header=True)
    pdf.set_font('Helvetica', '', 9)
    col_widths = [60, 130]
    
    constraints = [
        ('DuckDuckGo throttling', 'Backoff after 3 failures, 5-min cooldown'),
        ('Article fetch timeout', '6 second limit with TTL caching (1 hour)'),
        ('Rate limiting', '0.5s delay between enrichment attempts'),
        ('News aggregator URLs', 'news.google.com blocked from article body extraction'),
    ]
    
    for constraint, handling in constraints:
        pdf.cell(col_widths[0], 8, constraint, 1, 0, 'L')
        pdf.cell(col_widths[1], 8, handling, 1, 1, 'L')
    
    pdf.add_page()
    
    pdf.section_title('6. Tactical Pause Recommendation (Section 7.2)')
    
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(34, 197, 94)
    pdf.cell(0, 8, 'I agree with the 24-48 hour observation window.', 0, 1)
    pdf.set_text_color(30, 30, 30)
    pdf.ln(3)
    
    pdf.body_text('The triple-stack is deployed. Before we add:')
    pdf.bullet_point('Multi-pass recursion')
    pdf.bullet_point('Budget system')
    pdf.bullet_point('Reddit/Craigslist sources')
    pdf.bullet_point('Learning/meta-knowledge')
    
    pdf.ln(3)
    pdf.body_text('We need to know: Did NAMESTORM + DOMAINSTORM + PHONESTORM actually move the needle from 2% to 5%+?')
    
    pdf.ln(5)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(0, 8, 'Decision Framework:', 0, 1)
    pdf.ln(2)
    
    pdf.table_row('Outcome', 'What It Means', '', header=True)
    outcomes = [
        ('2% -> 5%', 'Triple-stack working, hitting news signal ceiling'),
        ('2% -> 8-10%', 'Home run - extraction layers adding real value'),
        ('Still ~2%', 'Problem is source data, not extraction'),
    ]
    
    for outcome, meaning in outcomes:
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(60, 8, outcome, 1, 0, 'L')
        pdf.cell(130, 8, meaning, 1, 1, 'L')
    
    pdf.ln(8)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(0, 8, 'Next Steps Based on Observation:', 0, 1)
    pdf.ln(2)
    
    pdf.body_text('If enrichment improves to 5%+:')
    pdf.bullet_point('The bottleneck is signal source quality')
    pdf.bullet_point('Expand SignalStorm (Reddit, Craigslist, job boards)')
    
    pdf.ln(3)
    pdf.body_text('If enrichment stays at ~2%:')
    pdf.bullet_point('The extraction layers need more work')
    pdf.bullet_point('Focus on improving NAMESTORM/DOMAINSTORM before adding complexity')
    
    pdf.ln(10)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    pdf.set_font('Helvetica', 'I', 9)
    pdf.set_text_color(100, 100, 100)
    pdf.multi_cell(0, 5, 'Document prepared by Melissa (Replit Agent) in response to Section 8 of the HossAgent Vision, Current State, and Enrichment Problem Statement alignment document.')
    
    output_path = 'attached_assets/HossAgent_Sanity_Check_Response.pdf'
    pdf.output(output_path)
    print(f"PDF generated: {output_path}")
    return output_path

if __name__ == "__main__":
    generate_pdf()
