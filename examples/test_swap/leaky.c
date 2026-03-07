#include <stdlib.h>
#include <string.h>


typedef struct s_pair
{
	char	*key;
	char	*value;
}	t_pair;

t_pair	*create_pair(const char *k, const char *v)
{
	t_pair	*p;

	p = malloc(sizeof(t_pair));
	p->key = malloc(strlen(k) + 1);
	strcpy(p->key, k);
	p->value = malloc(strlen(v) + 1);
	strcpy(p->value, v);
	return (p);
}

int	main(void)
{
	t_pair	*pair;

	pair = create_pair("name", "alice");
	free(pair->key);
	free(pair);
	return (0);
}
