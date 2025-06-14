from flask import Blueprint, request, session, redirect, url_for, render_template, flash, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, SelectField, BooleanField, SubmitField
from wtforms.validators import DataRequired, NumberRange, Optional, Email, ValidationError
from flask_login import current_user
from datetime import datetime
import uuid
import json
from extensions import db
from mailersend_email import send_email, EMAIL_CONFIG
from translations import trans
from models import FinancialHealth, log_tool_usage

financial_health_bp = Blueprint('financial_health', __name__, url_prefix='/financial_health')

class Step1Form(FlaskForm):
    first_name = StringField()
    email = StringField()
    user_type = SelectField()
    send_email = BooleanField()
    submit = SubmitField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lang = session.get('lang', 'en')
        self.first_name.label.text = trans('financial_health_first_name', lang=lang)
        self.email.label.text = trans('financial_health_email', lang=lang)
        self.user_type.label.text = trans('financial_health_user_type', lang=lang)
        self.send_email.label.text = trans('financial_health_send_email', lang=lang)
        self.submit.label.text = trans('financial_health_next', lang=lang)
        self.first_name.validators = [DataRequired(message=trans('financial_health_first_name_required', lang=lang))]
        self.email.validators = [Optional(), Email(message=trans('financial_health_email_invalid', lang=lang))]
        self.user_type.choices = [
            ('individual', trans('financial_health_user_type_individual', lang=lang)),
            ('business', trans('financial_health_user_type_business', lang=lang))
        ]

class Step2Form(FlaskForm):
    income = FloatField()
    expenses = FloatField()
    submit = SubmitField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lang = session.get('lang', 'en')
        self.income.label.text = trans('financial_health_monthly_income', lang=lang)
        self.expenses.label.text = trans('financial_health_monthly_expenses', lang=lang)
        self.submit.label.text = trans('financial_health_next', lang=lang)
        self.income.validators = [
            DataRequired(message=trans('financial_health_income_required', lang=lang)),
            NumberRange(min=0, max=10000000000, message=trans('financial_health_income_max', lang=lang))
        ]
        self.expenses.validators = [
            DataRequired(message=trans('financial_health_expenses_required', lang=lang)),
            NumberRange(min=0, max=10000000000, message=trans('financial_health_expenses_max', lang=lang))
        ]

    def validate_income(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data)
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid income input: {field.data}")
                raise ValidationError(trans('financial_health_income_invalid', lang=session.get('lang', 'en')))

    def validate_expenses(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data)
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid expenses input: {field.data}")
                raise ValidationError(trans('financial_health_expenses_invalid', lang=session.get('lang', 'en')))

class Step3Form(FlaskForm):
    debt = FloatField()
    interest_rate = FloatField()
    submit = SubmitField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lang = session.get('lang', 'en')
        self.debt.label.text = trans('financial_health_total_debt', lang=lang)
        self.interest_rate.label.text = trans('financial_health_average_interest_rate', lang=lang)
        self.submit.label.text = trans('financial_health_submit', lang=lang)
        self.debt.validators = [
            Optional(),
            NumberRange(min=0, max=10000000000, message=trans('financial_health_debt_max', lang=lang))
        ]
        self.interest_rate.validators = [
            Optional(),
            NumberRange(min=0, message=trans('financial_health_interest_rate_positive', lang=lang))
        ]

    def validate_debt(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data) if cleaned_data else None
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid debt input: {field.data}")
                raise ValidationError(trans('financial_health_debt_invalid', lang=session.get('lang', 'en')))

    def validate_interest_rate(self, field):
        if field.data is not None:
            try:
                cleaned_data = str(field.data).replace(',', '')
                field.data = float(cleaned_data) if cleaned_data else None
            except (ValueError, TypeError):
                current_app.logger.error(f"Invalid interest rate input: {field.data}")
                raise ValidationError(trans('financial_health_interest_rate_invalid', lang=session.get('lang', 'en')))

@financial_health_bp.route('/step1', methods=['GET', 'POST'])
def step1():
    """Handle financial health step 1 form (personal info)."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
    lang = session.get('lang', 'en')
    form_data = session.get('health_step1', {})
    if current_user.is_authenticated:
        form_data['email'] = form_data.get('email', current_user.email)
        form_data['first_name'] = form_data.get('first_name', current_user.username)
    form = Step1Form(data=form_data)
    current_app.logger.info(f"Starting step1 for session {session['sid']}")
    try:
        if request.method == 'POST':
            log_tool_usage(
                tool_name='financial_health',
                user_id=current_user.id if current_user.is_authenticated else None,
                session_id=session['sid'],
                action='step1_submit'
            )  # Moved outside transaction
            with db.session.begin():
                if not form.validate_on_submit():
                    current_app.logger.error(f"Form validation failed: {form.errors}")
                    flash(trans("financial_health_form_errors", lang=lang), "danger")
                    return render_template('HEALTHSCORE/health_score_step1.html', form=form, trans=trans, lang=lang)

                form_data = form.data.copy()
                if form_data.get('email') and not isinstance(form_data['email'], str):
                    current_app.logger.error(f"Invalid email type: {type(form_data['email'])}")
                    raise ValueError(trans("financial_health_email_must_be_string", lang=lang))

                # Check for existing record
                filter_kwargs = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
                financial_health = FinancialHealth.query.filter_by(**filter_kwargs, step=1).first()
                if not financial_health:
                    financial_health = FinancialHealth(
                        id=str(uuid.uuid4()),
                        user_id=current_user.id if current_user.is_authenticated else None,
                        session_id=session['sid'],
                        created_at=datetime.utcnow(),
                    )
                    db.session.add(financial_health)

                financial_health.step = 1
                financial_health.first_name = form_data['first_name']
                financial_health.email = form_data['email']
                financial_health.user_type = form_data['user_type']
                financial_health.send_email = form_data['send_email']

                current_app.logger.info(f"Step1 data updated/saved to database with ID {financial_health.id} for session {session['sid']}")

                session['health_step1'] = form_data
                session.modified = True
                return redirect(url_for('financial_health.step2'))
        log_tool_usage(
            tool_name='financial_health',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session['sid'],
            action='step1_view'
        )  # Moved outside transaction
        return render_template('HEALTHSCORE/health_score_step1.html', form=form, trans=trans, lang=lang)
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(f"Error in step1: {str(e)}")
        flash(f"{trans('financial_health_error_personal_info', lang=lang)} - {str(e)}", "danger")
        return render_template('HEALTHSCORE/health_score_step1.html', form=form, trans=trans, lang=lang), 500

@financial_health_bp.route('/step2', methods=['GET', 'POST'])
def step2():
    """Handle financial health step 2 form (income and expenses)."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
    lang = session.get('lang', 'en')
    if 'health_step1' not in session:
        flash(trans('financial_health_missing_step1', lang=lang, default='Please complete step 1 first.'), 'danger')
        return redirect(url_for('financial_health.step1'))
    form = Step2Form()
    current_app.logger.info(f"Starting step2 for session {session['sid']}")
    try:
        if request.method == 'POST':
            log_tool_usage(
                tool_name='financial_health',
                user_id=current_user.id if current_user.is_authenticated else None,
                session_id=session['sid'],
                action='step2_submit'
            )  # Moved outside transaction
            with db.session.begin():
                if not form.validate_on_submit():
                    current_app.logger.error(f"Form validation failed: {form.errors}")
                    flash(trans("financial_health_form_errors", lang=lang), "danger")
                    return render_template('HEALTHSCORE/health_score_step2.html', form=form, trans=trans, lang=lang)

                # Update existing record
                filter_kwargs = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
                financial_health = FinancialHealth.query.filter_by(**filter_kwargs, step=2).first()
                if not financial_health:
                    financial_health = FinancialHealth(
                        id=str(uuid.uuid4()),
                        user_id=current_user.id if current_user.is_authenticated else None,
                        session_id=session['sid'],
                        created_at=datetime.utcnow(),
                    )
                    db.session.add(financial_health)

                financial_health.step = 2
                financial_health.income = float(form.income.data)
                financial_health.expenses = float(form.expenses.data)

                current_app.logger.info(f"Step2 data updated/saved to database with ID {financial_health.id} for session {session['sid']}")

                session['health_step2'] = {
                    'income': float(form.income.data),
                    'expenses': float(form.expenses.data),
                }
                session.modified = True
                return redirect(url_for('financial_health.step3'))
        log_tool_usage(
            tool_name='financial_health',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session['sid'],
            action='step2_view'
        )  # Moved outside transaction
        return render_template('HEALTHSCORE/health_score_step2.html', form=form, trans=trans, lang=lang)
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(f"Error in step2: {str(e)}")
        flash(trans("financial_health_error_income_expenses", lang=lang), "danger")
        return render_template('HEALTHSCORE/health_score_step2.html', form=form, trans=trans, lang=lang), 500

@financial_health_bp.route('/step3', methods=['GET', 'POST'])
def step3():
    """Handle financial health step 3 form (debt and interest) and calculate score."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
    lang = session.get('lang', 'en')
    if 'health_step2' not in session:
        flash(trans('financial_health_missing_step2', lang=lang, default='Please complete step 2 first.'), "danger")
        return redirect(url_for('financial_health.step2'))
    form = Step3Form()
    current_app.logger.info(f"Starting step3 for session {session['sid']}")
    try:
        if request.method == 'POST':
            log_tool_usage(
                tool_name='financial_health',
                user_id=current_user.id if current_user.is_authenticated else None,
                session_id=session['sid'],
                action='step3_submit'
            )  # Moved outside transaction
            with db.session.begin():
                if not form.validate_on_submit():
                    current_app.logger.error(f"Form validation failed: {form.errors}")
                    flash(trans("financial_health_form_errors", lang=lang), "danger")
                    return render_template('HEALTHSCORE/health_score_step3.html', form=form, trans=trans, lang=lang)

                step1_data = session.get('health_step1', {})
                step2_data = session.get('health_step2', {})
                debt = float(form.debt.data) if form.debt.data else 0
                interest_rate = float(form.interest_rate.data) if form.interest_rate.data else 0
                income = step2_data.get('income', 0)
                expenses = step2_data.get('expenses', 0)

                if income <= 0:
                    current_app.logger.error("Income is zero or negative, cannot calculate financial health metrics")
                    flash(trans("financial_health_income_zero_error", lang=lang), "danger")
                    return render_template('HEALTHSCORE/health_score_step3.html', form=form, trans=trans, lang=lang), 500

                debt_to_income = (debt / income * 100) if income > 0 else 0
                savings_rate = ((income - expenses) / income * 100) if income > 0 else 0
                interest_burden = ((interest_rate * debt / 100) / 12) / income * 100 if debt > 0 and income > 0 else 0

                score = 100
                if debt_to_income > 0:
                    score -= min(debt_to_income / 50, 50)  # Corrected typo 'atlantic' to '/50'
                if savings_rate < 0:
                    score -= min(abs(savings_rate), 30)
                elif savings_rate > 0:
                    score += min(savings_rate / 2, 20)
                score -= min(interest_burden, 20)
                score = max(0, min(100, round(score)))

                if score >= 80:
                    status_key = "excellent"
                    status = trans("financial_health_status_excellent", lang=lang)
                elif score >= 60:
                    status_key = "good"
                    status = trans("financial_health_status_good", lang=lang)
                else:
                    status_key = "needs_improvement"
                    status = trans("financial_health_status_needs_improvement", lang=lang)

                badges = []
                if score >= 80:
                    badges.append(trans("financial_health_badge_financial_star", lang=lang))
                if debt_to_income < 20:
                    badges.append(trans("financial_health_badge_debt_manager", lang=lang))
                if savings_rate >= 20:
                    badges.append(trans("financial_health_badge_savings_pro", lang=lang))
                if interest_burden == 0 and debt > 0:
                    badges.append(trans("financial_health_badge_interest_free", lang=lang))

                # Update existing record
                filter_kwargs = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
                financial_health = FinancialHealth.query.filter_by(**filter_kwargs, step=3).first()
                if not financial_health:
                    financial_health = FinancialHealth(
                        id=str(uuid.uuid4()),
                        user_id=current_user.id if current_user.is_authenticated else None,
                        session_id=session['sid'],
                        created_at=datetime.utcnow(),
                    )
                    db.session.add(financial_health)

                financial_health.step = 3
                financial_health.first_name = step1_data.get('first_name', '')
                financial_health.email = step1_data.get('email', '')
                financial_health.user_type = step1_data.get('user_type', 'individual')
                financial_health.income = income
                financial_health.expenses = expenses
                financial_health.debt = debt
                financial_health.interest_rate = interest_rate
                financial_health.debt_to_income = debt_to_income
                financial_health.savings_rate = savings_rate
                financial_health.interest_burden = interest_burden
                financial_health.score = score
                financial_health.status = status
                financial_health.status_key = status_key
                financial_health.badges = json.dumps(badges)
                financial_health.send_email = step1_data.get('send_email', False)

                current_app.logger.info(f"Step3 data updated/saved to database with ID {financial_health.id} for session {session['sid']}")

                # Send email if opted in
                if step1_data.get('send_email', False) and step1_data.get('email'):
                    try:
                        config = EMAIL_CONFIG["financial_health"]
                        subject = trans(config["subject_key"], lang=lang)
                        template = config["template"]
                        send_email(
                            app=current_app,
                            logger=current_app.logger,
                            to_email=step1_data['email'],
                            subject=subject,
                            template_name=template,
                            data={
                                "first_name": step1_data['first_name'],
                                "score": score,
                                "status": status,
                                "income": income,
                                "expenses": expenses,
                                "debt": debt,
                                "interest_rate": interest_rate,
                                "debt_to_income": debt_to_income,
                                "savings_rate": savings_rate,
                                "interest_burden": interest_burden,
                                "badges": badges,
                                "created_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                "cta_url": url_for('financial_health.dashboard', _external=True)
                            },
                            lang=lang
                        )
                    except Exception as e:
                        current_app.logger.error(f"Failed to send email: {str(e)}")
                        flash(trans("financial_health_email_failed", lang=lang), "warning")

                session.pop('health_step1', None)
                session.pop('health_step2', None)
                session.modified = True
                flash(trans("financial_health_health_completed_success", lang=lang), "success")
                return redirect(url_for('financial_health.dashboard'))
        log_tool_usage(
            tool_name='financial_health',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session['sid'],
            action='step3_view'
        )  # Moved outside transaction
        return render_template('HEALTHSCORE/health_score_step3.html', form=form, trans=trans, lang=lang)
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(f"Error in step3: {str(e)}")
        flash(trans("financial_health_unexpected_error", lang=lang), "danger")
        return render_template('HEALTHSCORE/health_score_step3.html', form=form, trans=trans, lang=lang), 500

@financial_health_bp.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    """Display financial health dashboard with comparison to others."""
    if 'sid' not in session:
        session['sid'] = str(uuid.uuid4())
        session.permanent = True
    lang = session.get('lang', 'en')
    current_app.logger.info(f"Starting dashboard for session {session['sid']}")
    try:
        log_tool_usage(
            tool_name='financial_health',
            user_id=current_user.id if current_user.is_authenticated else None,
            session_id=session['sid'],
            action='dashboard_view'
        )
        # Query records from database for current user or session
        filter_kwargs = {'user_id': current_user.id} if current_user.is_authenticated else {'session_id': session['sid']}
        stored_records = FinancialHealth.query.filter_by(step=3, **filter_kwargs).order_by(FinancialHealth.created_at.desc()).all()
        if not stored_records:
            latest_record = {}
            records = []
        else:
            latest_record = stored_records[0].to_dict()
            records = [(record.id, record.to_dict()) for record in stored_records]

        # Query all records with step=3 for comparison
        all_records = FinancialHealth.query.filter_by(step=3).all()
        all_scores_for_comparison = [record.score for record in all_records if record.score is not None]

        total_users = len(all_scores_for_comparison)
        rank = 0
        average_score = 0
        if all_scores_for_comparison:
            all_scores_for_comparison.sort(reverse=True)
            user_score = latest_record.get("score", 0)
            rank = sum(1 for s in all_scores_for_comparison if s > user_score) + 1
            average_score = sum(all_scores_for_comparison) / total_users

        insights = []
        tips = [
            trans("financial_health_tip_track_expenses", lang=lang),
            trans("financial_health_tip_ajo_savings", lang=lang),
            trans("financial_health_tip_pay_debts", lang=lang),
            trans("financial_health_tip_plan_expenses", lang=lang)
        ]
        if latest_record:
            if latest_record.get('debt_to_income', 0) > 40:
                insights.append(trans("financial_health_insight_high_debt", lang=lang))
            if latest_record.get('savings_rate', 0) < 0:
                insights.append(trans("financial_health_insight_negative_savings", lang=lang))
            elif latest_record.get('savings_rate', 0) >= 20:
                insights.append(trans("financial_health_insight_good_savings", lang=lang))
            if latest_record.get('interest_burden', 0) > 10:
                insights.append(trans("financial_health_insight_high_interest", lang=lang))
            if total_users >= 5:
                if rank <= total_users * 0.1:
                    insights.append(trans("financial_health_insight_top_10", lang=lang))
                elif rank <= total_users * 0.3:
                    insights.append(trans("financial_health_insight_top_30", lang=lang))
                else:
                    insights.append(trans("financial_health_insight_below_30", lang=lang))
            else:
                insights.append(trans("financial_health_insight_not_enough_users", lang=lang))
        else:
            insights.append(trans("financial_health_insight_no_data", lang=lang))

        return render_template(
            'HEALTHSCORE/health_score_dashboard.html',
            records=records,
            latest_record=latest_record,
            insights=insights,
            tips=tips,
            rank=rank,
            total_users=total_users,
            average_score=average_score,
            trans=trans,
            lang=lang
        )
    except Exception as e:
        current_app.logger.exception(f"Critical error in dashboard: {str(e)}")
        flash(trans("financial_health_dashboard_load_error", lang=lang), "danger")
        return render_template(
            'HEALTHSCORE/health_score_dashboard.html',
            records=[],
            latest_record={},
            insights=[trans("financial_health_insight_no_data", lang=lang)],
            tips=[
                trans("financial_health_tip_track_expenses", lang=lang),
                trans("financial_health_tip_ajo_savings", lang=lang),
                trans("financial_health_tip_pay_debts", lang=lang),
                trans("financial_health_tip_plan_expenses", lang=lang)
            ],
            rank=0,
            total_users=0,
            average_score=0,
            trans=trans,
            lang=lang
        ), 500
