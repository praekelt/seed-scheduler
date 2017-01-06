from django import template

import seed_scheduler

register = template.Library()


@register.simple_tag
def current_version():
    return seed_scheduler.__version__
